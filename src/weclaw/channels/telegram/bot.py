from __future__ import annotations



import logging

from dataclasses import dataclass

from io import BytesIO

from datetime import datetime, timedelta, timezone



from telegram import BotCommand, Update

from telegram.error import BadRequest, NetworkError, TelegramError

from telegram.ext import (

    Application,

    CommandHandler,

    ContextTypes,

    MessageHandler,

    filters,

)



from weclaw.channels.telegram.access import is_owner

from weclaw.agents.runtime import run_agent_for_conversation

from weclaw.capabilities.catalog import build_tool_catalog_text, build_tool_detail_text, build_workflow_catalog_text, build_workflow_detail_text

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

from weclaw.core.response import AgentFile, AgentReply

from weclaw.agents.router import AgentServiceError, build_telegram_conversation

from weclaw.config import (
    TELEGRAM_API_RETRIES,
    TELEGRAM_API_RETRY_DELAY_SECONDS,
    TELEGRAM_BOOTSTRAP_RETRIES,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CONNECT_TIMEOUT,

    TELEGRAM_POLLING_TIMEOUT,

    TELEGRAM_POOL_TIMEOUT,

    TELEGRAM_READ_TIMEOUT,

    TELEGRAM_WRITE_TIMEOUT,

    SHOW_TOOL_TRACE,

)

from weclaw.media.store import FilePayload, load_photo_message, save_uploaded_file, save_voice_message

from weclaw.memory.intent import build_memory_intent_ack, detect_memory_intent, should_auto_accept_memory_intent

from weclaw.memory.store import (

    accept_memory_candidate,

    append_memory_candidate,

    append_structured_long_term_memory,

    clear_session_context,

    create_memory_metadata,

    list_memory_candidates,

    load_long_term_memory,

    reject_memory_candidate,

)

from weclaw.tasks.service import handle_task_command

from weclaw.memory.session import clear_session_id

from weclaw.skills.store import get_skill, list_skills

from weclaw.media.speech import SpeechRecognitionError, transcribe_voice

from weclaw.channels.telegram.formatting import format_telegram_text
from weclaw.core.model_profiles import render_model_profiles, resolve_model_profile_selector, set_active_model_profile
from weclaw.core.provider_model import get_effective_model, get_provider_mode_label
from weclaw.core.provider_state import get_provider
from weclaw.decision.render import render_decision_plan_debug

from weclaw.decision.router import build_decision_plan



logger = logging.getLogger(__name__)


def _telegram_exc_info(exc: BaseException) -> tuple[type[BaseException], BaseException, object]:
    return (type(exc), exc, exc.__traceback__)


async def call_telegram_api_with_retry(operation: str, call):
    attempts = max(TELEGRAM_API_RETRIES, 0) + 1
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except NetworkError as exc:
            if attempt >= attempts:
                raise
            delay = TELEGRAM_API_RETRY_DELAY_SECONDS * attempt
            logger.warning(
                "Telegram API %s failed on attempt %s/%s; retrying in %.1fs.",
                operation,
                attempt,
                attempts,
                delay,
                exc_info=_telegram_exc_info(exc),
            )
            import asyncio

            await asyncio.sleep(delay)


@dataclass(slots=True)
class TelegramMessageSender:
    bot: object

    async def send_text(self, target_id: str, text: str) -> None:
        await call_telegram_api_with_retry(
            "send message",
            lambda: self.bot.send_message(chat_id=int(target_id), text=text),
        )


    async def send_message(self, chat_id: str | int, text: str) -> None:

        await self.send_text(str(chat_id), text)



    async def send_file(self, chat_id: str | int, file_data: bytes, file_name: str) -> None:
        from io import BytesIO

        async def send_document() -> object:
            document = BytesIO(file_data)
            document.name = file_name
            return await self.bot.send_document(
                chat_id=int(chat_id),
                document=document,
                filename=file_name,
            )

        await call_telegram_api_with_retry(
            "send document",
            send_document,
        )


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





async def reply_plain_text(update: Update, text: str) -> None:

    """发送纯文本回复，用于错误提示或格式化回退。"""



    if not update.message:

        return



    try:
        await call_telegram_api_with_retry(
            "reply plain text",
            lambda: update.message.reply_text(text, disable_web_page_preview=True),
        )
    except NetworkError as exc:
        logger.warning("Telegram plain text reply failed because the network is unavailable", exc_info=_telegram_exc_info(exc))
    except TelegramError as exc:
        logger.warning("Telegram plain text reply failed", exc_info=_telegram_exc_info(exc))




async def reply_formatted_text(update: Update, text: str) -> None:

    """优先发送格式化文本，失败时回退到纯文本。"""



    if not update.message:

        return



    for chunk in format_telegram_text(text):

        try:
            await call_telegram_api_with_retry(
                "reply formatted text",
                lambda: update.message.reply_text(
                    chunk["text"],
                    parse_mode=chunk["parse_mode"],
                    disable_web_page_preview=True,
                ),
            )
        except BadRequest:
            logger.warning("Telegram formatted reply failed, falling back to plain text", exc_info=True)
            await reply_plain_text(update, text)
            return
        except NetworkError as exc:
            logger.warning("Telegram formatted reply failed because the network is unavailable", exc_info=_telegram_exc_info(exc))
            return
        except TelegramError as exc:
            logger.warning("Telegram formatted reply failed", exc_info=_telegram_exc_info(exc))
            return




async def reply_agent_result(update: Update, reply: AgentReply) -> None:

    """统一发送 Agent 返回结果：先发图片和文件，再发文本说明。"""



    if not update.message:

        return



    for image in reply.images:
        image_name = f"generated.{image.mime_type.removeprefix('image/')}"

        async def send_photo(data=image.data, name=image_name, caption=image.caption):
            image_file = BytesIO(data)
            image_file.name = name
            return await update.message.reply_photo(photo=image_file, caption=caption)

        await call_telegram_api_with_retry(
            "reply photo",
            send_photo,
        )

    for f in reply.files:
        async def send_document(data=f.data, name=f.file_name):
            file_obj = BytesIO(data)
            file_obj.name = name
            return await update.message.reply_document(document=file_obj)

        await call_telegram_api_with_retry(
            "reply document",
            send_document,
        )


    if reply.text.strip():

        await reply_formatted_text(update, reply.text)





async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """处理普通文本消息。"""



    if not update.message or not update.message.text:

        return

    if not is_owner(update):

        return



    try:

        text = update.message.text.strip()

        lower_text = text.lower()

        pending = get_pending_confirmation(update.effective_chat.id) if update.effective_chat else None

        if pending is not None:

            decision = normalize_confirmation_reply(text)

            if decision is None:

                await reply_plain_text(update, "当前有待确认的工具操作，请回复“允许”“本会话允许”或“拒绝”。")

            else:

                if decision == "approve_session":

                    granted = grant_session_tool_access(pending.request.session_scope, pending.request)

                    resolve_pending_confirmation(update.effective_chat.id, True)

                    if granted:

                        await reply_plain_text(update, f"已允许并记住当前会话授权：{pending.request.tool_name}")

                    else:

                        await reply_plain_text(update, "当前工具不支持会话级授权，已按单次确认继续执行。")

                else:

                    approved = decision == "approve_once"

                    resolve_pending_confirmation(update.effective_chat.id, approved)

                    await reply_plain_text(update, "已确认，继续执行。" if approved else "已取消本次工具操作。")

            return

        if update.effective_chat is not None:

            task_result = await handle_task_command(build_telegram_conversation(update), text)

            if task_result.handled:

                await reply_plain_text(update, task_result.message)

                return



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

                await reply_plain_text(update, build_memory_intent_ack(memory_intent, True, SHOW_TOOL_TRACE, target.name))

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

                await reply_plain_text(update, build_memory_intent_ack(memory_intent, False, SHOW_TOOL_TRACE, candidate_file.name))

            return



        sender = TelegramMessageSender(update.get_bot())

        response = await run_agent_for_conversation(

            prompt=update.message.text,

            conversation=build_telegram_conversation(update),

            sender=sender,

        )

        await reply_agent_result(update, response)

    except AgentServiceError as exc:

        await reply_plain_text(update, f"抱歉，这次调用模型服务失败了：{exc}")

    except NetworkError:

        logger.warning("Telegram network error while handling message", exc_info=True)

        await reply_plain_text(update, "抱歉，当前网络连接不稳定，请稍后重试。")

    except TelegramError:

        logger.exception("Telegram API error while handling message")

        await reply_plain_text(update, "抱歉，消息发送失败了。请稍后重试。")

    except Exception:

        logger.exception("Unexpected error while handling message")

        await reply_plain_text(update, "抱歉，机器人刚刚出了点问题。请稍后再试。")





async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """处理图片消息：图片先进入内存，再直接交给 Agent。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    try:

        photo_payload = await load_photo_message(update.message)

        caption = (update.message.caption or "").strip()

        caption_text = caption or "无"

        prompt = (

            "用户上传了一张图片。\n"

            f"用户附带说明：{caption_text}\n\n"

            "请结合图片和用户说明进行分析，并直接给出有帮助的中文回答。"

        )

        record_text = f"用户上传了一张图片。说明：{caption_text}"

        sender = TelegramMessageSender(update.get_bot())

        response = await run_agent_for_conversation(

            prompt=prompt,

            conversation=build_telegram_conversation(update),

            sender=sender,

            record_text=record_text,

            uploaded_image=photo_payload,

        )

        await reply_agent_result(update, response)

    except AgentServiceError as exc:

        await reply_plain_text(update, f"抱歉，这次图片处理失败了：{exc}")

    except NetworkError:

        logger.warning("Telegram network error while handling photo", exc_info=True)

        await reply_plain_text(update, "抱歉，当前网络连接不稳定，图片处理失败。请稍后重试。")

    except TelegramError:

        logger.exception("Telegram API error while handling photo")

        await reply_plain_text(update, "抱歉，图片下载或消息发送失败了。请稍后重试。")

    except Exception:

        logger.exception("Unexpected error while handling photo")

        await reply_plain_text(update, "抱歉，机器人处理图片时出了点问题。请稍后再试。")





async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """处理语音消息：先转写，再把文字内容交给 Agent。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    try:

        voice_path = await save_voice_message(update.message)

        transcript = transcribe_voice(voice_path)

        prompt = (

            "用户发送了一条语音消息。\n"

            f"语音本地路径：{voice_path}\n"

            f"语音转写文本：{transcript}\n\n"

            "请把这条语音转写文本当作用户的真实输入来处理。"

        )

        sender = TelegramMessageSender(update.get_bot())

        response = await run_agent_for_conversation(

            prompt=prompt,

            conversation=build_telegram_conversation(update),

            sender=sender,

        )

        await reply_agent_result(update, response)

    except SpeechRecognitionError as exc:

        logger.warning("Speech recognition failed", exc_info=True)

        await reply_plain_text(update, f"语音已保存，但语音转文字失败：{exc}")

    except AgentServiceError as exc:

        await reply_plain_text(update, f"语音已转写，但这次调用模型服务失败了：{exc}")

    except NetworkError:

        logger.warning("Telegram network error while handling voice", exc_info=True)

        await reply_plain_text(update, "抱歉，当前网络连接不稳定，语音处理失败。请稍后重试。")

    except TelegramError:

        logger.exception("Telegram API error while handling voice")

        await reply_plain_text(update, "抱歉，语音下载或消息发送失败了。请稍后重试。")

    except Exception:

        logger.exception("Unexpected error while handling voice")

        await reply_plain_text(update, "抱歉，机器人处理语音时出了点问题。请稍后再试。")





async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """处理文档/文件消息：下载到工作区，交给 Agent 分析。"""



    if not update.message or not update.message.document:

        return

    if not is_owner(update):

        return



    try:

        doc = update.message.document

        telegram_file = await doc.get_file()

        data = await telegram_file.download_as_bytearray()

        file_name = doc.file_name or "file"



        file_payload = save_uploaded_file(bytes(data), file_name,

                                          doc.mime_type or "application/octet-stream")

        caption = (update.message.caption or "").strip() or "无"



        prompt = (

            f"用户上传了一个文件：{file_payload.file_name}\n"

            f"用户附带说明：{caption}\n\n"

            f"文件已保存到：{file_payload.saved_path}\n"

            "请使用 read_workspace_file 工具读取并分析该文件，"

            "然后直接给出有帮助的中文回答。"

        )

        record_text = f"[Telegram] 用户上传了一个文件：{file_payload.file_name}。说明：{caption}"



        sender = TelegramMessageSender(update.get_bot())

        response = await run_agent_for_conversation(

            prompt=prompt,

            conversation=build_telegram_conversation(update),

            sender=sender,

            record_text=record_text,

            uploaded_file=file_payload,

        )

        await reply_agent_result(update, response)

    except AgentServiceError as exc:

        await reply_plain_text(update, f"抱歉，这次文件处理失败了：{exc}")

    except NetworkError:

        logger.warning("Telegram network error while handling document", exc_info=True)

        await reply_plain_text(update, "抱歉，当前网络连接不稳定，文件处理失败。请稍后重试。")

    except TelegramError:

        logger.exception("Telegram API error while handling document")

        await reply_plain_text(update, "抱歉，文件下载或消息发送失败了。请稍后重试。")

    except Exception:

        logger.exception("Unexpected error while handling document")

        await reply_plain_text(update, "抱歉，机器人处理文件时出了点问题。请稍后再试。")





async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """响应 /start，介绍当前机器人支持的主要能力。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    await update.message.reply_text(

        "你好，我是你的机器人。\n\n"

        "我可以回答问题、处理文字、图片和语音消息，使用 Claude 内置工具，操作工作区，并继续之前保存的会话。\n"

        "还支持定时任务，例如“30秒后提醒我喝水”“每天下午3点提醒我站起来活动一下”。\n"

        "可以使用 /memory 查看长期记忆，使用 /reset 清空当前会话。\n"

        "使用 /skills 查看当前可用的 skills，/tools 查看当前可用工具，/workflows 查看当前可用 workflow，/plan 查看路由计划。\n"

        "使用 /grants 查看当前会话工具授权，/revoke 工具名 撤销自动授权。\n"

        "使用 /schedule_in、/tasks、/cancel 管理定时任务。"

    )





async def reset_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """清空当前 session，让下一条消息从新会话开始。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    session_scope = build_telegram_conversation(update).session_scope

    clear_session_id(session_scope)

    clear_session_context(session_scope)

    cleared_grants = clear_session_tool_grants(session_scope)

    suffix = f" 已清除 {cleared_grants} 个工具授权。" if cleared_grants else ""

    await update.message.reply_text(f"当前会话已清空，下一条消息会开启新会话。{suffix}")





async def show_grants(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    if not update.message:

        return

    if not is_owner(update):

        return



    session_scope = build_telegram_conversation(update).session_scope

    grants = list_session_tool_grants(session_scope)

    if not grants:

        await reply_plain_text(update, "当前会话没有已授权自动执行的工具。")

        return



    lines = ["当前会话工具授权："]

    for grant in grants:

        lines.append(f"- {grant.tool_name} [{grant.risk_level}/{grant.category}] 授权于 {grant.granted_at}")

    await reply_plain_text(update, "\n".join(lines))





async def revoke_grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    if not update.message:

        return

    if not is_owner(update):

        return

    if not context.args:

        await reply_plain_text(update, "用法：/revoke 工具名")

        return



    tool_name = context.args[0].strip()

    session_scope = build_telegram_conversation(update).session_scope

    if revoke_session_tool_grant(session_scope, tool_name):

        await reply_plain_text(update, f"已撤销当前会话对工具 {tool_name} 的自动授权。")

        return

    await reply_plain_text(update, f"当前会话没有找到工具 {tool_name} 的授权记录。")





async def show_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """查看当前长期记忆内容。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    await reply_plain_text(update, load_long_term_memory())





async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """把用户指定内容写入候选记忆区，避免直接污染正式长期记忆。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    memory_note = " ".join(context.args).strip()

    if not memory_note:

        await reply_plain_text(update, "用法：/remember 这里填写要写入长期记忆的内容")

        return



    candidate_file = append_memory_candidate(

        memory_note,

        metadata=create_memory_metadata(category="general", source="manual_remember", confidence="medium"),

    )

    await reply_plain_text(update, f"已写入候选记忆区，等待后续确认：\n- {candidate_file.name}")





async def show_memory_candidates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """查看当前候选记忆列表。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    candidates = list_memory_candidates()

    if not candidates:

        await reply_plain_text(update, "当前没有候选记忆。")

        return



    lines = ["当前候选记忆："]

    for path in candidates:

        lines.append(f"- {path.name}")

    await reply_plain_text(update, "\n".join(lines))





async def accept_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """接受一条候选记忆，并合并到正式长期记忆或结构化类别。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    if not context.args:

        await reply_plain_text(update, "用法：/memory_accept 文件名 [profile|preferences|rules|general]")

        return



    name = context.args[0].strip()

    category = context.args[1].strip().lower() if len(context.args) > 1 else "general"

    try:

        target = accept_memory_candidate(name, category)

    except FileNotFoundError:

        await reply_plain_text(update, f"没有找到候选记忆：{name}")

        return



    await reply_plain_text(update, f"已采纳候选记忆：\n- {name}\n- 目标：{target.name}")





async def reject_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """拒绝并删除一条候选记忆。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    if not context.args:

        await reply_plain_text(update, "用法：/memory_reject 文件名")

        return



    name = context.args[0].strip()

    try:

        reject_memory_candidate(name)

    except FileNotFoundError:

        await reply_plain_text(update, f"没有找到候选记忆：{name}")

        return



    await reply_plain_text(update, f"已拒绝并删除候选记忆：\n- {name}")





async def show_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """查看当前可用的 skills，或查看某个 skill 的详细说明。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    if not context.args:

        lines = ["当前可用的 skills："]

        for skill in list_skills():

            lines.append(f"- {skill.name}：{skill.description}")

        lines.append("\n你也可以发送 /skills skill_name 查看单个 skill 的详情。")

        await reply_plain_text(update, "\n".join(lines))

        return



    skill_name = context.args[0].strip().lower()

    skill = get_skill(skill_name)

    if skill is None:

        await reply_plain_text(update, f"没有找到名为 {skill_name} 的 skill。")

        return



    detail = skill.file_path.read_text(encoding="utf-8").strip() if skill.file_path.exists() else "这个 skill 文件暂时不存在。"

    await reply_plain_text(

        update,

        f"Skill: {skill.name}\n"

        f"标题：{skill.title}\n"

        f"说明：{skill.description}\n\n"

        f"{detail}",

    )





async def show_tools(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """查看当前 Provider 可用工具，或查看单个工具详情。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    tool_ctx = ToolContext(sender=None, target_id=str(update.effective_chat.id) if update.effective_chat else "telegram")

    provider = get_provider()

    if not context.args:

        await reply_plain_text(update, build_tool_catalog_text(provider=provider, context=tool_ctx))

        return



    tool_name = context.args[0].strip()

    detail = build_tool_detail_text(tool_name, provider=provider, context=tool_ctx)

    if detail is None:

        await reply_plain_text(update, f"没有找到名为 {tool_name} 的工具，或当前 Provider 不可用。")

        return

    await reply_plain_text(update, detail)





async def show_workflows(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """查看当前可用的 workflows，或查看某个 workflow 的详细说明。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    if not context.args:

        await reply_plain_text(update, build_workflow_catalog_text())

        return



    workflow_name = context.args[0].strip()

    detail = build_workflow_detail_text(workflow_name)

    if detail is None:

        await reply_plain_text(update, f"没有找到名为 {workflow_name} 的 workflow。")

        return

    await reply_plain_text(update, detail)





async def show_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    if not update.message:

        return

    if not is_owner(update):

        return

    request_text = " ".join(context.args).strip()

    if not request_text:

        await reply_plain_text(update, "用法：/plan 这里填写要分析的请求")

        return

    conversation = build_telegram_conversation(update)

    plan = await build_decision_plan(

        prompt=request_text,

        provider=get_provider(),

        session_scope=conversation.session_scope,

        channel=conversation.channel,

    )

    await reply_plain_text(update, render_decision_plan_debug(plan))





async def schedule_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """通过命令创建一个单次定时任务。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    if len(context.args) < 2:

        await reply_plain_text(update, "用法：/schedule_in 秒数 任务内容")

        return



    try:

        delay_seconds = int(context.args[0])

    except ValueError:

        await reply_plain_text(update, "秒数必须是整数，例如：/schedule_in 60 1分钟后提醒我喝水")

        return



    if delay_seconds <= 0:

        await reply_plain_text(update, "秒数必须大于 0。")

        return



    prompt = " ".join(context.args[1:]).strip()

    if not prompt:

        await reply_plain_text(update, "任务内容不能为空。")

        return



    if update.effective_chat is None:

        await reply_plain_text(update, "当前消息没有可用的 chat_id。")

        return



    run_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)

    conversation = build_telegram_conversation(update)

    task_id = await create_scheduled_task(conversation, prompt, run_at)

    local_time = run_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    await reply_plain_text(

        update,

        "定时任务已创建。\n"

        f"- 任务ID：{task_id}\n"

        "- 类型：单次任务\n"

        f"- 执行时间：{local_time}\n"

        f"- 内容：{prompt}",

    )





async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """列出当前待执行的定时任务。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    task_result = await handle_task_command(build_telegram_conversation(update), "/tasks")

    await reply_plain_text(update, task_result.message)





async def cancel_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """取消一个未执行的定时任务。"""



    if not update.message:

        return

    if not is_owner(update):

        return



    task_result = await handle_task_command(build_telegram_conversation(update), f"/cancel {' '.join(context.args)}".strip())

    await reply_plain_text(update, task_result.message)





async def show_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:

        return

    if not is_owner(update):

        return

    provider = get_provider()
    await reply_plain_text(
        update,
        "\n".join(
            [
                f"当前 Provider: {provider}",
                f"接口类型: {get_provider_mode_label(provider)}",
                f"当前模型: {get_effective_model(provider) or '(empty)'}",
            ]
        ),
    )


async def handle_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not is_owner(update):
        return
    provider = get_provider()
    if not context.args:
        await reply_plain_text(update, render_model_profiles())
        return
    args = list(context.args)
    if args[0].lower() == "use":
        args = args[1:]
    if not args:
        await reply_plain_text(update, render_model_profiles())
        return
    selector = args[0].strip()
    model = " ".join(args[1:]).strip() or None
    try:
        profile_id = resolve_model_profile_selector(selector).id
        profile = set_active_model_profile(profile_id, model)
    except ValueError as exc:
        await reply_plain_text(update, str(exc))
        return
    await reply_plain_text(update, f"已切换到 {profile.id}\n当前模型: {profile.model or '(empty)'}")




async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:

    """兜底记录没有被局部 handler 处理的异常。"""



    if isinstance(context.error, NetworkError):
        logger.warning("Telegram network error outside a local handler", exc_info=_telegram_exc_info(context.error))
        return

    logger.error("Unhandled exception in Telegram application", exc_info=context.error)




async def post_init(application: Application) -> None:

    """启动后设置 Telegram 命令菜单。"""

    commands = [

        BotCommand("start", "启动与能力介绍"),

        BotCommand("skills", "查看可用技能"),

        BotCommand("tools", "查看可用工具"),

        BotCommand("workflows", "查看工作流"),

        BotCommand("plan", "查看当前路由计划"),

        BotCommand("grants", "查看会话工具授权"),

        BotCommand("revoke", "撤销工具授权"),

        BotCommand("memory", "查看长期记忆"),

        BotCommand("remember", "写入候选记忆"),

        BotCommand("reset", "清空当前会话"),

        BotCommand("provider", "查看当前模型"),
        BotCommand("model", "查看或切换模型 Provider"),
        BotCommand("schedule_in", "创建定时任务"),

        BotCommand("tasks", "列出定时任务"),

        BotCommand("cancel", "取消定时任务"),

    ]

    await application.bot.set_my_commands(commands)

    logger.info("Telegram command menu set successfully")





def build_application() -> Application:

    """创建 Telegram 应用并注册命令、消息和错误处理器。"""



    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError("TELEGRAM_BOT_TOKEN is required when starting the Telegram bot.")



    app = (

        Application.builder()

        .token(TELEGRAM_BOT_TOKEN)

        .connect_timeout(TELEGRAM_CONNECT_TIMEOUT)

        .read_timeout(TELEGRAM_READ_TIMEOUT)

        .write_timeout(TELEGRAM_WRITE_TIMEOUT)

        .pool_timeout(TELEGRAM_POOL_TIMEOUT)

        .get_updates_connect_timeout(TELEGRAM_CONNECT_TIMEOUT)

        .get_updates_read_timeout(TELEGRAM_READ_TIMEOUT)

        .get_updates_write_timeout(TELEGRAM_WRITE_TIMEOUT)

        .get_updates_pool_timeout(TELEGRAM_POOL_TIMEOUT)

        .post_init(post_init)

        .build()

    )

    app.add_handler(CommandHandler("start", start))

    app.add_handler(CommandHandler("memory", show_memory))

    app.add_handler(CommandHandler("remember", remember))

    app.add_handler(CommandHandler("memory_candidates", show_memory_candidates))

    app.add_handler(CommandHandler("memory_accept", accept_memory))

    app.add_handler(CommandHandler("memory_reject", reject_memory))

    app.add_handler(CommandHandler("skills", show_skills))

    app.add_handler(CommandHandler("tools", show_tools))

    app.add_handler(CommandHandler("workflows", show_workflows))

    app.add_handler(CommandHandler("plan", show_plan))

    app.add_handler(CommandHandler("grants", show_grants))

    app.add_handler(CommandHandler("revoke", revoke_grant))

    app.add_handler(CommandHandler("reset", reset_session))

    app.add_handler(CommandHandler("provider", show_provider))
    app.add_handler(CommandHandler("model", handle_model))
    app.add_handler(CommandHandler("schedule_in", schedule_in))

    app.add_handler(CommandHandler("tasks", list_tasks))

    app.add_handler(CommandHandler("cancel", cancel_task))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(error_handler)

    return app





def run_polling_options() -> dict[str, int]:

    """集中提供 Telegram 轮询启动参数。"""



    return {

        "timeout": TELEGRAM_POLLING_TIMEOUT,

        "bootstrap_retries": TELEGRAM_BOOTSTRAP_RETRIES,

    }
