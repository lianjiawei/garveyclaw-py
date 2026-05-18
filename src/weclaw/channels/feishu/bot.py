from __future__ import annotations



import asyncio

import json

import logging

from collections import deque

from dataclasses import dataclass

from datetime import datetime, timedelta, timezone

from io import BytesIO

from typing import Any



import lark_oapi as lark

from lark_oapi.api.im.v1 import (

    CreateFileRequest,

    CreateFileRequestBody,

    CreateImageRequest,

    CreateImageRequestBody,

    CreateMessageRequest,

    CreateMessageRequestBody,

    GetMessageResourceRequest,

    P2ImMessageReceiveV1,

)



from weclaw.agents.router import AgentServiceError, build_feishu_conversation

from weclaw.agents.runtime import run_agent_for_conversation

from weclaw.capabilities.catalog import build_tool_catalog_text, build_tool_detail_text, build_workflow_catalog_text, build_workflow_detail_text

from weclaw.capabilities.runtime import start_background_capability_watcher, stop_background_capability_watcher

from weclaw.capabilities.tools import ToolContext

from weclaw.core.confirmation import (

    ToolConfirmationRequest,

    clear_session_tool_grants,

    grant_session_tool_access,

    get_pending_confirmation,

    list_session_tool_grants,

    normalize_confirmation_reply,

    register_pending_confirmation,

    revoke_session_tool_grant,

    resolve_pending_confirmation,

    wait_for_pending_confirmation,

)

from weclaw.core.response import AgentReply

from weclaw.config import (

    FEISHU_ALLOWED_CHAT_IDS,
    FEISHU_ALLOWED_OPEN_IDS,
    FEISHU_API_RETRIES,
    FEISHU_API_RETRY_DELAY_SECONDS,
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,

    FEISHU_REPLY_PROCESSING_MESSAGE,

    FEISHU_SESSION_SCOPE_PREFIX,

    SHOW_TOOL_TRACE,

)

from weclaw.channels.feishu.formatting import markdown_to_lark_md
from weclaw.core.model_profiles import render_model_profiles, set_active_model_profile
from weclaw.core.provider_model import get_effective_model, get_provider_mode_label
from weclaw.core.provider_state import get_provider
from weclaw.decision.render import render_decision_plan_debug

from weclaw.decision.router import build_decision_plan

from weclaw.media.speech import SpeechRecognitionError, transcribe_voice

from weclaw.media.store import FilePayload, PhotoPayload, save_uploaded_file, save_voice_bytes

from weclaw.config import UPLOAD_VOICES_DIR

from weclaw.memory.intent import build_memory_intent_ack, detect_memory_intent, should_auto_accept_memory_intent

from weclaw.memory.store import (

    accept_memory_candidate,

    append_memory_candidate,

    append_structured_long_term_memory,

    create_memory_metadata,

    list_memory_candidates,

    load_long_term_memory,

    reject_memory_candidate,

)

from weclaw.memory.store import clear_session_context

from weclaw.tasks.service import handle_task_command

from weclaw.memory.session import clear_session_id

from weclaw.skills.store import get_skill, list_skills



logger = logging.getLogger(__name__)





def parse_csv_set(value: str) -> set[str]:

    return {item.strip() for item in value.split(",") if item.strip()}





ALLOWED_OPEN_IDS = parse_csv_set(FEISHU_ALLOWED_OPEN_IDS)

ALLOWED_CHAT_IDS = parse_csv_set(FEISHU_ALLOWED_CHAT_IDS)

SEEN_MESSAGE_IDS: deque[str] = deque(maxlen=1000)


async def call_feishu_api_with_retry(operation: str, call):
    attempts = max(FEISHU_API_RETRIES, 0) + 1
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except Exception:
            if attempt >= attempts:
                raise
            delay = FEISHU_API_RETRY_DELAY_SECONDS * attempt
            logger.warning(
                "Feishu API %s failed on attempt %s/%s; retrying in %.1fs.",
                operation,
                attempt,
                attempts,
                delay,
                exc_info=True,
            )
            await asyncio.sleep(delay)


def raise_if_feishu_response_failed(response, operation: str) -> None:
    if not response.success():
        raise RuntimeError(f"Feishu {operation} failed: code={response.code}, msg={response.msg}")


# 飞书交互式卡片消息使用 lark_md 标签，原生支持 Markdown 渲染。





@dataclass(slots=True)

class FeishuIncomingMessage:

    message_id: str

    chat_id: str

    sender_open_id: str

    chat_type: str

    text: str = ""

    image_key: str | None = None

    file_key: str | None = None

    file_name: str | None = None

    voice_key: str | None = None





@dataclass(slots=True)

class FeishuBotAdapter:

    client: lark.Client



    async def send_text(self, target_id: str, text: str) -> None:

        await send_text_message(self.client, target_id, text)



    async def send_message(self, chat_id: str | int, text: str) -> None:

        await self.send_text(str(chat_id), text)



    async def send_file(self, chat_id: str | int, file_data: bytes, file_name: str) -> None:

        file_key = await upload_file_message(self.client, file_data, file_name)

        await send_file_message(self.client, str(chat_id), file_key)



    async def confirm_tool_use(self, target_id: str, request: ToolConfirmationRequest) -> bool:

        try:

            register_pending_confirmation(target_id, request)

        except RuntimeError:

            await self.send_text(target_id, "当前已有待确认的工具操作，请先回复 允许、本会话允许 或 拒绝。")

            return False



        lines = [

            request.prompt,

            f"工具：{request.tool_name}",

            f"类别：{request.category}",

            f"风险：{request.risk_level}",

        ]

        if request.summary:

            lines.append(f"摘要：{request.summary}")

        lines.append("请回复“允许”仅执行一次；回复“本会话允许”后续本会话同工具自动执行；回复“拒绝”取消。")

        await self.send_text(target_id, "\n".join(lines))

        try:

            return await wait_for_pending_confirmation(target_id)

        except TimeoutError:

            await self.send_text(target_id, "工具确认已超时，当前操作已取消。")

            return False





def ensure_feishu_config() -> None:

    if not FEISHU_APP_ID:

        raise RuntimeError("FEISHU_APP_ID is required when starting the Feishu bot.")

    if not FEISHU_APP_SECRET:

        raise RuntimeError("FEISHU_APP_SECRET is required when starting the Feishu bot.")





def build_feishu_client() -> lark.Client:

    ensure_feishu_config()

    return lark.Client.builder().app_id(FEISHU_APP_ID).app_secret(FEISHU_APP_SECRET).build()





def build_session_scope(message: FeishuIncomingMessage) -> str:

    if message.chat_type == "p2p":

        return f"{FEISHU_SESSION_SCOPE_PREFIX}:p2p:{message.sender_open_id}"

    return f"{FEISHU_SESSION_SCOPE_PREFIX}:chat:{message.chat_id}"





def is_allowed_message(message: FeishuIncomingMessage) -> bool:

    if not ALLOWED_OPEN_IDS and not ALLOWED_CHAT_IDS:

        return True

    return message.sender_open_id in ALLOWED_OPEN_IDS or message.chat_id in ALLOWED_CHAT_IDS





def is_duplicate(message_id: str) -> bool:

    if not message_id:

        return False

    if message_id in SEEN_MESSAGE_IDS:

        return True

    SEEN_MESSAGE_IDS.append(message_id)

    return False





def extract_text_content(raw_content: str) -> str:

    try:

        content = json.loads(raw_content or "{}")

    except json.JSONDecodeError:

        return raw_content.strip()



    text = content.get("text")

    return text.strip() if isinstance(text, str) else ""





def get_nested_attr(obj: Any, path: str, default: Any = "") -> Any:

    current = obj

    for name in path.split("."):

        current = getattr(current, name, None)

        if current is None:

            return default

    return current





def parse_incoming_message(data: P2ImMessageReceiveV1) -> FeishuIncomingMessage | None:

    event = getattr(data, "event", None)

    message = getattr(event, "message", None)

    if message is None:

        return None



    message_type = getattr(message, "message_type", "")

    if message_type not in {"text", "image", "file", "audio"}:

        return None



    if message_type == "image":

        content = json.loads(getattr(message, "content", "{}") or "{}")

        image_key = content.get("image_key", "")

        if not image_key:

            return None

        return FeishuIncomingMessage(

            message_id=getattr(message, "message_id", ""),

            chat_id=getattr(message, "chat_id", ""),

            sender_open_id=get_nested_attr(event, "sender.sender_id.open_id"),

            chat_type=getattr(message, "chat_type", ""),

            image_key=image_key,

        )



    if message_type == "file":

        content = json.loads(getattr(message, "content", "{}") or "{}")

        file_key = content.get("file_key", "")

        file_name = content.get("file_name", "file")

        if not file_key:

            return None

        return FeishuIncomingMessage(

            message_id=getattr(message, "message_id", ""),

            chat_id=getattr(message, "chat_id", ""),

            sender_open_id=get_nested_attr(event, "sender.sender_id.open_id"),

            chat_type=getattr(message, "chat_type", ""),

            text=extract_text_content(getattr(message, "content", "")),

            file_key=file_key,

            file_name=file_name,

        )



    if message_type == "audio":

        content = json.loads(getattr(message, "content", "{}") or "{}")

        voice_key = content.get("file_key", "")

        if not voice_key:

            return None

        return FeishuIncomingMessage(

            message_id=getattr(message, "message_id", ""),

            chat_id=getattr(message, "chat_id", ""),

            sender_open_id=get_nested_attr(event, "sender.sender_id.open_id"),

            chat_type=getattr(message, "chat_type", ""),

            voice_key=voice_key,

        )



    return FeishuIncomingMessage(

        message_id=getattr(message, "message_id", ""),

        chat_id=getattr(message, "chat_id", ""),

        sender_open_id=get_nested_attr(event, "sender.sender_id.open_id"),

        chat_type=getattr(message, "chat_type", ""),

        text=extract_text_content(getattr(message, "content", "")),

    )





async def download_image(client: lark.Client, message_id: str, file_key: str) -> bytes:

    """把飞书图片下载到内存。"""



    request = (

        GetMessageResourceRequest.builder()

        .message_id(message_id)

        .file_key(file_key)

        .type("image")

        .build()

    )

    response = await client.im.v1.message_resource.aget(request)

    if response.file is not None:

        return response.file.read()

    raw = getattr(response, "raw", None)

    raw_content = getattr(raw, "content", b"") if raw else b""

    detail = raw_content.decode("utf-8", errors="replace") if raw_content else ""

    raise RuntimeError(f"Feishu image download failed: code={response.code}, msg={response.msg}, detail={detail}")





async def download_file(client: lark.Client, message_id: str, file_key: str) -> bytes:

    """把飞书文件下载到内存。"""



    request = (

        GetMessageResourceRequest.builder()

        .message_id(message_id)

        .file_key(file_key)

        .type("file")

        .build()

    )

    response = await client.im.v1.message_resource.aget(request)

    if response.file is not None:

        return response.file.read()

    raw = getattr(response, "raw", None)

    raw_content = getattr(raw, "content", b"") if raw else b""

    detail = raw_content.decode("utf-8", errors="replace") if raw_content else ""

    raise RuntimeError(f"Feishu file download failed: code={response.code}, msg={response.msg}, detail={detail}")





async def send_text_message(client: lark.Client, chat_id: str, text: str) -> None:

    """用飞书交互卡片发送 Markdown 渲染消息。"""



    formatted = markdown_to_lark_md(text)

    if not formatted:

        return



    card = {

        "config": {"wide_screen_mode": True},

        "elements": [

            {"tag": "markdown", "content": formatted},

        ],

    }



    request = (

        CreateMessageRequest.builder()

        .receive_id_type("chat_id")

        .request_body(

            CreateMessageRequestBody.builder()

            .receive_id(chat_id)

            .msg_type("interactive")

            .content(json.dumps(card, ensure_ascii=False))

            .build()

        )

        .build()

    )



    response = await call_feishu_api_with_retry("send message", lambda: client.im.v1.message.acreate(request))
    raise_if_feishu_response_failed(response, "send message")




async def upload_image_message(client: lark.Client, image_data: bytes, mime_type: str) -> str:

    """把本地图片上传到飞书，返回 image_key。"""



    image_type = "message"

    if mime_type.lower() in {"image/png", "image/x-png"}:

        image_type = "message"



    request = (

        CreateImageRequest.builder()

        .request_body(

            CreateImageRequestBody.builder()

            .image_type(image_type)

            .image(BytesIO(image_data))

            .build()

        )

        .build()

    )



    response = await call_feishu_api_with_retry("upload image", lambda: client.im.v1.image.acreate(request))
    if not response.success() or response.data is None or not response.data.image_key:
        raise RuntimeError(f"Feishu upload image failed: code={response.code}, msg={response.msg}")
    return response.data.image_key





async def send_image_message(client: lark.Client, chat_id: str, image_key: str) -> None:

    request = (

        CreateMessageRequest.builder()

        .receive_id_type("chat_id")

        .request_body(

            CreateMessageRequestBody.builder()

            .receive_id(chat_id)

            .msg_type("image")

            .content(json.dumps({"image_key": image_key}, ensure_ascii=False))

            .build()

        )

        .build()

    )



    response = await call_feishu_api_with_retry("send image message", lambda: client.im.v1.message.acreate(request))
    raise_if_feishu_response_failed(response, "send image message")




async def upload_file_message(client: lark.Client, file_data: bytes, file_name: str) -> str:

    """把本地文件上传到飞书，返回 file_key。"""



    import mimetypes

    mime_type, _ = mimetypes.guess_type(file_name)

    mime_type = mime_type or "application/octet-stream"



    request = (

        CreateFileRequest.builder()

        .request_body(

            CreateFileRequestBody.builder()

            .file_name(file_name)

            .file(BytesIO(file_data))

            .file_type(mime_type)

            .build()

        )

        .build()

    )



    response = await call_feishu_api_with_retry("upload file", lambda: client.im.v1.file.acreate(request))
    if not response.success() or response.data is None or not response.data.file_key:
        raise RuntimeError(f"Feishu upload file failed: code={response.code}, msg={response.msg}")
    return response.data.file_key





async def send_file_message(client: lark.Client, chat_id: str, file_key: str) -> None:

    request = (

        CreateMessageRequest.builder()

        .receive_id_type("chat_id")

        .request_body(

            CreateMessageRequestBody.builder()

            .receive_id(chat_id)

            .msg_type("file")

            .content(json.dumps({"file_key": file_key}, ensure_ascii=False))

            .build()

        )

        .build()

    )



    response = await call_feishu_api_with_retry("send file message", lambda: client.im.v1.message.acreate(request))
    raise_if_feishu_response_failed(response, "send file message")




async def reply_agent_result(client: lark.Client, chat_id: str, reply: AgentReply) -> None:

    if reply.text.strip():

        await send_text_message(client, chat_id, reply.text)



    if reply.images:

        for image in reply.images:

            image_key = await upload_image_message(client, image.data, image.mime_type)

            await send_image_message(client, chat_id, image_key)



    if reply.files:

        for f in reply.files:

            file_key = await upload_file_message(client, f.data, f.file_name)

            await send_file_message(client, chat_id, file_key)





async def handle_message(client: lark.Client, incoming: FeishuIncomingMessage) -> None:

    if is_duplicate(incoming.message_id):

        logger.info("Skip duplicate Feishu message: %s", incoming.message_id)

        return



    if not incoming.text and not incoming.image_key and not incoming.file_key and not incoming.voice_key:

        return



    if not is_allowed_message(incoming):

        logger.info("Skip unauthorized Feishu message: sender=%s chat=%s", incoming.sender_open_id, incoming.chat_id)

        return



    if incoming.text.strip().lower() == "/reset":

        session_scope = build_session_scope(incoming)

        clear_session_id(session_scope)

        clear_session_context(session_scope)

        cleared_grants = clear_session_tool_grants(session_scope)

        suffix = f" 已清除 {cleared_grants} 个工具授权。" if cleared_grants else ""

        await send_text_message(client, incoming.chat_id, f"当前会话已清空，下一条消息会开启新会话。{suffix}")

        return



    lower_text = incoming.text.strip().lower()

    pending = get_pending_confirmation(incoming.chat_id) if incoming.text.strip() else None

    if pending is not None:

        decision = normalize_confirmation_reply(incoming.text)

        if decision is None:

            await send_text_message(client, incoming.chat_id, "当前有待确认的工具操作，请回复“允许”“本会话允许”或“拒绝”。")

        else:

            if decision == "approve_session":

                granted = grant_session_tool_access(pending.request.session_scope, pending.request)

                resolve_pending_confirmation(incoming.chat_id, True)

                if granted:

                    await send_text_message(client, incoming.chat_id, f"已允许并记住当前会话授权：{pending.request.tool_name}")

                else:

                    await send_text_message(client, incoming.chat_id, "当前工具不支持会话级授权，已按单次确认继续执行。")

            else:

                approved = decision == "approve_once"

                resolve_pending_confirmation(incoming.chat_id, approved)

                await send_text_message(client, incoming.chat_id, "已确认，继续执行。" if approved else "已取消本次工具操作。")

        return

    if lower_text == "/provider":
        provider = get_provider()
        await send_text_message(
            client,
            incoming.chat_id,
            "\n".join(
                [
                    f"当前 Provider: {provider}",
                    f"接口类型: {get_provider_mode_label(provider)}",
                    f"当前模型: {get_effective_model(provider) or '(empty)'}",
                ]
            ),
        )
        return
    if lower_text.startswith("/model"):
        args = incoming.text.strip().split(maxsplit=1)
        if len(args) == 1:
            await send_text_message(client, incoming.chat_id, render_model_profiles())
        else:
            parts = args[1].strip().split()
            if not parts or parts[0].lower() != "use" or len(parts) < 2:
                await send_text_message(client, incoming.chat_id, "用法: /model use <profile_id> [model]")
                return
            try:
                profile = set_active_model_profile(parts[1], " ".join(parts[2:]) if len(parts) > 2 else None)
            except ValueError as exc:
                await send_text_message(client, incoming.chat_id, str(exc))
                return
            await send_text_message(client, incoming.chat_id, f"已切换到 {profile.id}\n当前模型: {profile.model or '(empty)'}")
        return


    if lower_text == "/start":

        await send_text_message(client, incoming.chat_id,

            "你好，我是你的机器人。\n\n"

            "我可以回答问题、处理文字、图片和文件消息，使用模型内置工具，操作工作区，并继续之前保存的会话。\n"

            "支持定时任务、长期记忆管理和自定义技能。\n"

            "使用 /skills 查看可用技能，/tools 查看可用工具，/workflows 查看可用 workflow，/plan 查看路由计划，/memory 查看长期记忆，/reset 清空当前会话。\n"

            "使用 /grants 查看当前会话工具授权，/revoke 工具名 撤销自动授权。"

        )

        return



    if lower_text.startswith("/plan"):

        args = incoming.text.strip().split(maxsplit=1)

        if len(args) == 1 or not args[1].strip():

            await send_text_message(client, incoming.chat_id, "用法：/plan 这里填写要分析的请求")

        else:

            session_scope = build_session_scope(incoming)

            plan = await build_decision_plan(

                prompt=args[1].strip(),

                provider=get_provider(),

                session_scope=session_scope,

                channel="feishu",

            )

            await send_text_message(client, incoming.chat_id, render_decision_plan_debug(plan))

        return



    if lower_text == "/grants":

        session_scope = build_session_scope(incoming)

        grants = list_session_tool_grants(session_scope)

        if not grants:

            await send_text_message(client, incoming.chat_id, "当前会话没有已授权自动执行的工具。")

        else:

            lines = ["当前会话工具授权："]

            for grant in grants:

                lines.append(f"- {grant.tool_name} [{grant.risk_level}/{grant.category}] 授权于 {grant.granted_at}")

            await send_text_message(client, incoming.chat_id, "\n".join(lines))

        return



    if lower_text.startswith("/revoke"):

        args = incoming.text.strip().split(maxsplit=1)

        if len(args) == 1 or not args[1].strip():

            await send_text_message(client, incoming.chat_id, "用法：/revoke 工具名")

        else:

            session_scope = build_session_scope(incoming)

            tool_name = args[1].strip()

            if revoke_session_tool_grant(session_scope, tool_name):

                await send_text_message(client, incoming.chat_id, f"已撤销当前会话对工具 {tool_name} 的自动授权。")

            else:

                await send_text_message(client, incoming.chat_id, f"当前会话没有找到工具 {tool_name} 的授权记录。")

        return



    if lower_text.startswith("/skills"):

        args = incoming.text.strip().split(maxsplit=1)

        if len(args) == 1:

            lines = ["当前可用的 skills："]

            for skill in list_skills():

                lines.append(f"- {skill.name}：{skill.description}")

            lines.append("\n发送 /skills 技能名 查看详情。")

            await send_text_message(client, incoming.chat_id, "\n".join(lines))

        else:

            skill = get_skill(args[1].strip().lower())

            if skill is None:

                await send_text_message(client, incoming.chat_id, f"没有找到名为 {args[1].strip()} 的 skill。")

            elif not skill.file_path.exists():

                await send_text_message(client, incoming.chat_id, f"Skill '{skill.name}' 的文件暂时不存在。")

            else:

                detail = skill.file_path.read_text(encoding="utf-8").strip()

                await send_text_message(client, incoming.chat_id,

                    f"Skill: {skill.name}\n标题：{skill.title}\n说明：{skill.description}\n\n{detail}"

                )

        return



    if lower_text.startswith("/tools"):

        args = incoming.text.strip().split(maxsplit=1)

        tool_ctx = ToolContext(sender=None, target_id=incoming.chat_id)

        provider = get_provider()

        if len(args) == 1:

            await send_text_message(client, incoming.chat_id, build_tool_catalog_text(provider=provider, context=tool_ctx))

        else:

            tool_name = args[1].strip()

            detail = build_tool_detail_text(tool_name, provider=provider, context=tool_ctx)

            if detail is None:

                await send_text_message(client, incoming.chat_id, f"没有找到名为 {tool_name} 的工具，或当前 Provider 不可用。")

            else:

                await send_text_message(client, incoming.chat_id, detail)

        return



    if lower_text.startswith("/workflows"):

        args = incoming.text.strip().split(maxsplit=1)

        if len(args) == 1:

            await send_text_message(client, incoming.chat_id, build_workflow_catalog_text())

        else:

            workflow_name = args[1].strip()

            detail = build_workflow_detail_text(workflow_name)

            if detail is None:

                await send_text_message(client, incoming.chat_id, f"没有找到名为 {workflow_name} 的 workflow。")

            else:

                await send_text_message(client, incoming.chat_id, detail)

        return



    if lower_text == "/memory":

        await send_text_message(client, incoming.chat_id, load_long_term_memory())

        return



    if lower_text == "/memory_candidates":

        candidates = list_memory_candidates()

        if not candidates:

            await send_text_message(client, incoming.chat_id, "当前没有候选记忆。")

        else:

            lines = ["当前候选记忆："]

            for path in candidates:

                lines.append(f"- {path.name}")

            await send_text_message(client, incoming.chat_id, "\n".join(lines))

        return



    if lower_text.startswith("/remember"):

        memory_note = " ".join(incoming.text.strip().split()[1:]).strip()

        if not memory_note:

            await send_text_message(client, incoming.chat_id, "用法：/remember 这里填写要写入长期记忆的内容")

        else:

            candidate_file = append_memory_candidate(

                memory_note,

                metadata=create_memory_metadata(category="general", source="manual_remember", confidence="medium"),

            )

            await send_text_message(client, incoming.chat_id, f"已写入候选记忆区，等待后续确认：\n- {candidate_file.name}")

        return



    if lower_text.startswith("/memory_accept"):

        args = incoming.text.strip().split()

        if len(args) < 2:

            await send_text_message(client, incoming.chat_id, "用法：/memory_accept 文件名 [profile|preferences|rules|general]")

        else:

            name = args[1].strip()

            category = args[2].strip().lower() if len(args) > 2 else "general"

            try:

                target = accept_memory_candidate(name, category)

            except FileNotFoundError:

                await send_text_message(client, incoming.chat_id, f"没有找到候选记忆：{name}")

                return

            await send_text_message(client, incoming.chat_id, f"已采纳候选记忆：\n- {name}\n- 目标：{target.name}")

        return



    if lower_text.startswith("/memory_reject"):

        args = incoming.text.strip().split()

        if len(args) < 2:

            await send_text_message(client, incoming.chat_id, "用法：/memory_reject 文件名")

        else:

            name = args[1].strip()

            try:

                reject_memory_candidate(name)

            except FileNotFoundError:

                await send_text_message(client, incoming.chat_id, f"没有找到候选记忆：{name}")

                return

            await send_text_message(client, incoming.chat_id, f"已拒绝并删除候选记忆：\n- {name}")

        return



    conversation = build_feishu_conversation(incoming, build_session_scope(incoming))

    text = incoming.text.strip()

    lower_text = text.lower()



    if lower_text.startswith("/schedule") or lower_text.startswith("/schedule_in") or lower_text.startswith("/cancel") or lower_text == "/tasks":

        task_result = await handle_task_command(conversation, text)

        if task_result.handled:

            await send_text_message(client, incoming.chat_id, task_result.message)

            return



    task_result = await handle_task_command(conversation, text)

    if task_result.handled:

        await send_text_message(client, incoming.chat_id, task_result.message)

        return



    if FEISHU_REPLY_PROCESSING_MESSAGE:

        await send_text_message(client, incoming.chat_id, "收到，正在处理...")



    bot = FeishuBotAdapter(client)

    photo_payload = None

    file_payload = None

    prompt: str

    record_text: str

    try:

        if incoming.voice_key:

            voice_data = await download_file(client, incoming.message_id, incoming.voice_key)

            voice_path = save_voice_bytes(voice_data)

            transcript = transcribe_voice(voice_path)

            prompt = (

                "用户发送了一条语音消息。\n"

                f"语音本地路径：{voice_path}\n"

                f"语音转写文本：{transcript}\n\n"

                "请把这条语音转写文本当作用户的真实输入来处理。"

            )

            record_text = f"[Feishu] 用户发送了一条语音。转写：{transcript}"

        elif incoming.file_key:

            file_data = await download_file(client, incoming.message_id, incoming.file_key)

            file_payload = save_uploaded_file(file_data, incoming.file_name or "file", "application/octet-stream")

            caption = incoming.text or "无"

            prompt = (

                f"用户上传了一个文件：{file_payload.file_name}\n"

                f"用户附带说明：{caption}\n\n"

                f"文件已保存到：{file_payload.saved_path}\n"

                "请使用 read_workspace_file 工具读取并分析该文件，"

                "然后直接给出有帮助的中文回答。"

            )

            record_text = f"[Feishu] 用户上传了一个文件：{file_payload.file_name}。说明：{caption}"

        elif incoming.image_key:

            image_data = await download_image(client, incoming.message_id, incoming.image_key)

            photo_payload = PhotoPayload(data=image_data, mime_type="image/jpeg")

            caption = incoming.text or "无"

            prompt = (

                "用户上传了一张图片。\n"

                f"用户附带说明：{caption}\n\n"

                "请先调用 get_uploaded_image 工具获取本轮图片内容，"

                "再结合图片和用户说明进行分析，并直接给出有帮助的中文回答。"

            )

            record_text = f"[Feishu] 用户上传了一张图片。说明：{caption}"

        else:

            memory_intent = detect_memory_intent(text)

            if memory_intent is not None:

                if should_auto_accept_memory_intent(memory_intent):

                    target = append_structured_long_term_memory(

                        memory_intent.content,

                        memory_intent.category,

                        memory_intent.slot,

                        create_memory_metadata(

                            category=memory_intent.category,

                            slot=memory_intent.slot,

                            reason=memory_intent.reason,

                            source="user_explicit",

                            confidence=memory_intent.confidence,

                        ),

                    )

                    await send_text_message(client, incoming.chat_id, build_memory_intent_ack(memory_intent, True, SHOW_TOOL_TRACE, target.name))

                else:

                    candidate_file = append_memory_candidate(

                        memory_intent.content,

                        memory_intent.category,

                        memory_intent.reason,

                        memory_intent.slot,

                        create_memory_metadata(

                            category=memory_intent.category,

                            slot=memory_intent.slot,

                            reason=memory_intent.reason,

                            source="user_candidate",

                            confidence=memory_intent.confidence,

                        ),

                    )

                    await send_text_message(client, incoming.chat_id, build_memory_intent_ack(memory_intent, False, SHOW_TOOL_TRACE, candidate_file.name))

                return

            prompt = text

            record_text = f"[Feishu] {text}"

            photo_payload = None

            file_payload = None



        reply = await run_agent_for_conversation(

            prompt=prompt,

            conversation=conversation,

            sender=bot,

            continue_session=True,

            record_text=record_text,

            uploaded_image=photo_payload,

            uploaded_file=file_payload,

        )

        await reply_agent_result(client, incoming.chat_id, reply)

    except AgentServiceError as exc:

        await send_text_message(client, incoming.chat_id, f"抱歉，这次调用模型服务失败了：{exc}")

    except SpeechRecognitionError as exc:

        logger.warning("Feishu speech recognition failed: %s", exc)

        await send_text_message(client, incoming.chat_id, f"语音已保存，但语音转文字失败：{exc}")

    except Exception as exc:

        logger.exception("Feishu message handling failed")

        await send_text_message(client, incoming.chat_id, f"抱歉，飞书通道处理失败了：{exc}")





def build_event_handler(client: lark.Client):

    def on_message(data: P2ImMessageReceiveV1) -> None:

        incoming = parse_incoming_message(data)

        if incoming is None:

            return

        try:

            loop = asyncio.get_running_loop()

        except RuntimeError:

            asyncio.run(handle_message(client, incoming))

        else:

            loop.create_task(handle_message(client, incoming))



    return lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()





def main() -> None:

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    capability_watcher = start_background_capability_watcher()

    client = build_feishu_client()

    event_handler = build_event_handler(client)

    ws_client = lark.ws.Client(

        app_id=FEISHU_APP_ID,

        app_secret=FEISHU_APP_SECRET,

        event_handler=event_handler,

        log_level=lark.LogLevel.INFO,

        auto_reconnect=True,

    )

    print("Feishu bot is running with WebSocket long connection...")

    try:

        ws_client.start()

    finally:

        stop_background_capability_watcher(capability_watcher)





if __name__ == "__main__":

    main()
