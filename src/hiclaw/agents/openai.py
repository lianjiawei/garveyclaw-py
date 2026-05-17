from __future__ import annotations

import base64
import io
import json
import logging
import re
from typing import Any
from urllib.parse import urljoin

import httpx

from hiclaw.capabilities.tools import list_openai_tool_names, parse_openai_allowed_tools
from hiclaw.core.response import AgentImage, AgentReply
from hiclaw.decision.models import DecisionPlan
from hiclaw.decision.render import render_decision_plan
from hiclaw.config import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_IMAGE_API_KEY,
    OPENAI_IMAGE_BASE_URL,
    OPENAI_IMAGE_EDIT_PATH,
    OPENAI_IMAGE_GENERATE_PATH,
    OPENAI_IMAGE_INCLUDE_OPTIONAL_PARAMS,
    OPENAI_IMAGE_MODEL,
    OPENAI_IMAGE_OUTPUT_FORMAT,
    OPENAI_IMAGE_QUALITY,
    OPENAI_IMAGE_SIZE,
    OPENAI_IMAGE_TIMEOUT_SECONDS,
    WORKSPACE_DIR,
)
from hiclaw.core.delivery import MessageSender
from hiclaw.core.provider_model import get_effective_api_key, get_effective_base_url, get_effective_model
from hiclaw.core.types import ConversationRef
from hiclaw.memory.store import append_conversation_record, build_context_snapshot
from hiclaw.core.locks import acquire_runtime_lock
from hiclaw.skills.store import build_skill_prompt
from hiclaw.agents.openai_stream import collect_chat_sse_response
from hiclaw.agents.openai_tools import OpenAIToolContext, build_openai_tools, execute_openai_tool

logger = logging.getLogger(__name__)

IMAGE_REQUEST_KEYWORDS = (
    "生成图片",
    "生成一张",
    "画一张",
    "做一张图",
    "做图",
    "生图",
    "改图",
    "编辑图片",
    "修改图片",
    "换成图片",
    "头像",
    "海报",
    "插画",
)

IMAGE_REQUEST_PATTERNS = (
    re.compile(r"\b(generate|create|make|draw|edit)\s+(an?\s+)?(image|picture|photo|poster|illustration|avatar)\b"),
    re.compile(r"\b(image|picture|photo|poster|illustration|avatar)\s+(generation|editing|edit)\b"),
)

INTERNAL_TEXT_TASK_MARKERS = (
    "[AgentTask:",
    "## Output Contract",
    "Task ID:",
)


class OpenAIImageRequestError(RuntimeError):
    """图片生成/编辑接口失败时，给 Telegram 展示更可读的错误原因。"""


def get_image_api_key() -> str:
    """图片接口可以单独配置 key；不配置时复用文本 OpenAI key。"""

    api_key = OPENAI_IMAGE_API_KEY or get_effective_api_key("openai") or OPENAI_API_KEY
    if not api_key:
        raise RuntimeError(
            "OpenAI-compatible image API key is not configured. "
            "Run `python -m hiclaw setup`, or add a provider with `python -m hiclaw model add --protocol openai ...`."
        )
    return api_key


def build_image_url(path: str) -> str:
    """构造图片接口地址，兼容服务商自定义路径。"""

    base_url = OPENAI_IMAGE_BASE_URL or get_effective_base_url("openai") or OPENAI_BASE_URL
    if not base_url:
        raise RuntimeError(
            "OpenAI-compatible image base URL is not configured. "
            "Official OpenAI users can set OPENAI_BASE_URL, and third-party providers usually provide a /v1 endpoint."
        )
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def build_image_error_message(exc: httpx.HTTPStatusError) -> str:
    """把服务商 HTTP 错误转换成不泄露密钥的中文提示。"""

    status_code = exc.response.status_code
    response_text = exc.response.text.strip()
    if len(response_text) > 500:
        response_text = response_text[:500] + "..."

    detail = f" 服务商返回：{response_text}" if response_text else ""
    if status_code == 400:
        return f"图片接口参数错误：服务商不接受当前请求参数，可能需要调整模型名、尺寸或字段。{detail}"
    if status_code == 401:
        return f"图片接口鉴权失败：请检查 OPENAI_IMAGE_API_KEY / OPENAI_API_KEY 是否是图片接口可用的 key。{detail}"
    if status_code == 403:
        return f"图片接口拒绝访问：可能是余额不足、图片能力未开通，或当前 key 没有图片权限。{detail}"
    if status_code == 404:
        return f"图片接口路径不存在：请检查 OPENAI_IMAGE_BASE_URL 和 OPENAI_IMAGE_GENERATE_PATH / OPENAI_IMAGE_EDIT_PATH。{detail}"
    if status_code == 504:
        return f"图片接口网关超时：请求已到达服务商，但服务商后端生成图片超时。可以稍后重试，或降低图片尺寸/换图片模型。{detail}"
    return f"图片接口调用失败：HTTP {status_code}。{detail}"


async def parse_image_response(response: httpx.Response) -> dict[str, Any]:
    """统一处理图片接口响应，保留清晰错误信息。"""

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise OpenAIImageRequestError(build_image_error_message(exc)) from exc

    try:
        return response.json()
    except ValueError as exc:
        raise OpenAIImageRequestError("图片接口返回的不是合法 JSON，可能不是 OpenAI 兼容图片接口。") from exc


def extract_user_image_prompt(prompt: str, record_text: str | None) -> str:
    """图片生成优先使用用户原始说明，避免把内部工具提示词传给生图模型。"""

    if record_text and "说明：" in record_text:
        user_prompt = record_text.split("说明：", maxsplit=1)[1].strip()
        if user_prompt and user_prompt != "无":
            return user_prompt
    return prompt.strip()


def wants_image_output(prompt: str, record_text: str | None, uploaded_image: Any | None) -> bool:
    """判断本轮是否应该走 OpenAI 图片生成/编辑，而不是普通文本回答。"""

    if record_text and record_text.startswith("[AgentTask:"):
        return False
    if all(marker in prompt for marker in INTERNAL_TEXT_TASK_MARKERS):
        return False

    user_prompt = extract_user_image_prompt(prompt, record_text).lower()
    if any(keyword.lower() in user_prompt for keyword in IMAGE_REQUEST_KEYWORDS):
        return True
    if any(pattern.search(user_prompt) for pattern in IMAGE_REQUEST_PATTERNS):
        return True

    return False


def build_openai_instructions(prompt: str, session_scope: str | None = None, decision_plan: DecisionPlan | None = None) -> str:
    """构造 OpenAI chat/completions 使用的系统提示。"""

    context_snapshot = build_context_snapshot(session_scope, prompt)
    selected_skills, skill_prompt = build_skill_prompt(prompt, decision_plan.selected_skills if decision_plan is not None else None)
    selected_skill_names = ", ".join(skill.name for skill in selected_skills) or "无"
    tool_names = list_openai_tool_names(allowed_names=parse_openai_allowed_tools())
    tool_list_text = "、".join(f"`{name}`" for name in tool_names)
    decision_text = render_decision_plan(decision_plan)

    return f"""
你现在运行在一个多入口个人智能体系统中。
当前工作区目录是：{WORKSPACE_DIR}

下面是当前可用的分层上下文快照：
{context_snapshot}

本轮命中的 skill：{selected_skill_names}

{skill_prompt}

{decision_text}

规则：
1. 回答尽量使用自然、清晰的中文。
2. 本模式当前可用工具有：{tool_list_text}。
3. 当用户询问当前时间时，优先调用 `get_current_time`。
4. 当用户需要联网搜索信息（天气、新闻、百科等）时，必须调用 `web_search`，禁止用 curl、wget、bash 等任何方式直接爬取网页替代搜索。
5. 如果需要额外主动给当前会话发送一条消息，请调用 `send_message`。
6. 可以使用文件查看、编辑、任务管理和 Bash 工具。
7. 如果工具足以回答问题，先调用工具，再基于工具结果给出最终回答。
8. 如果工具不可用或没有必要，不要虚构工具结果。
9. **Shell 平台适配**：`bash` 工具已按操作系统自动选择 shell。在 Linux/macOS 上直接编写 Bash 命令；在 Windows 上编写 PowerShell 命令。Windows 下使用 PowerShell 等效命令（Move-Item、Remove-Item、Get-ChildItem、Get-Content、Select-String 等）。
""".strip()


def build_chat_messages(prompt: str, uploaded_image: Any | None) -> list[dict[str, Any]]:
    if uploaded_image is None:
        return [{"role": "user", "content": prompt}]

    image_data = base64.b64encode(uploaded_image.data).decode("ascii")
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{uploaded_image.mime_type};base64,{image_data}"},
                },
            ],
        }
    ]


def build_chat_headers() -> dict[str, str]:
    api_key = get_effective_api_key("openai")
    if not api_key:
        raise RuntimeError(
            "OpenAI-compatible API key is not configured. "
            "Run `python -m hiclaw setup`, or add one with `python -m hiclaw model add --protocol openai ...`."
        )
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def extract_generated_images_from_payload(payload: dict[str, Any]) -> list[AgentImage]:
    """兼容标准 OpenAI Images 响应，以及部分服务商的简化响应格式。"""

    data = payload.get("data")
    if data is None:
        data = payload.get("images") or payload.get("image")
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    images: list[AgentImage] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        b64_json = item.get("b64_json") or item.get("base64") or item.get("image_base64")
        if not b64_json:
            image_url = item.get("url")
            if not image_url:
                continue
            async with httpx.AsyncClient(timeout=OPENAI_IMAGE_TIMEOUT_SECONDS) as client:
                response = await client.get(str(image_url), follow_redirects=True)
                response.raise_for_status()
            mime_type = response.headers.get("content-type", f"image/{OPENAI_IMAGE_OUTPUT_FORMAT}").split(";", 1)[0]
            images.append(AgentImage(data=response.content, mime_type=mime_type))
            continue
        if "," in b64_json and b64_json.lstrip().startswith("data:"):
            b64_json = b64_json.split(",", maxsplit=1)[1]
        images.append(
            AgentImage(
                data=base64.b64decode(b64_json),
                mime_type=f"image/{OPENAI_IMAGE_OUTPUT_FORMAT}",
            )
        )
    return images


def build_image_file(uploaded_image: Any) -> io.BytesIO:
    """把 Telegram 图片 bytes 包装成 OpenAI SDK 可上传的内存文件。"""

    suffix = "jpg" if uploaded_image.mime_type == "image/jpeg" else OPENAI_IMAGE_OUTPUT_FORMAT
    image_file = io.BytesIO(uploaded_image.data)
    image_file.name = f"telegram_upload.{suffix}"
    return image_file


async def stream_chat_completion(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    timeout_hint: str,
) -> Any:
    async with client.stream(
        "POST",
        f"{get_effective_base_url('openai').rstrip('/')}/chat/completions",
        headers=headers,
        json=payload,
    ) as response:
        if response.status_code != 200:
            error_text = await response.aread()
            raise RuntimeError(
                f"OpenAI {timeout_hint} chat/completions failed: HTTP {response.status_code} - "
                f"{error_text.decode('utf-8', errors='replace')[:500]}"
            )
        return await collect_chat_sse_response(response)


async def call_image_generate_api(image_prompt: str) -> dict[str, Any]:
    """直接调用图片生成接口，便于适配非标准 OpenAI 中转服务。"""

    payload = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": image_prompt,
        "size": OPENAI_IMAGE_SIZE,
    }
    if OPENAI_IMAGE_INCLUDE_OPTIONAL_PARAMS:
        payload.update(
            {
                "n": 1,
                "quality": OPENAI_IMAGE_QUALITY,
                "output_format": OPENAI_IMAGE_OUTPUT_FORMAT,
                "response_format": "b64_json",
            }
        )
    headers = {"Authorization": f"Bearer {get_image_api_key()}"}
    async with httpx.AsyncClient(timeout=OPENAI_IMAGE_TIMEOUT_SECONDS) as client:
        response = await client.post(build_image_url(OPENAI_IMAGE_GENERATE_PATH), headers=headers, json=payload)
        return await parse_image_response(response)


async def call_image_edit_api(image_prompt: str, uploaded_image: Any) -> dict[str, Any]:
    """直接调用图片编辑接口；图片以内存文件 multipart 上传。"""

    data = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": image_prompt,
        "size": OPENAI_IMAGE_SIZE,
    }
    if OPENAI_IMAGE_INCLUDE_OPTIONAL_PARAMS:
        data.update(
            {
                "n": "1",
                "quality": OPENAI_IMAGE_QUALITY,
                "output_format": OPENAI_IMAGE_OUTPUT_FORMAT,
                "response_format": "b64_json",
            }
        )
    files = {
        "image": (
            build_image_file(uploaded_image).name,
            uploaded_image.data,
            uploaded_image.mime_type,
        )
    }
    headers = {"Authorization": f"Bearer {get_image_api_key()}"}
    async with httpx.AsyncClient(timeout=OPENAI_IMAGE_TIMEOUT_SECONDS) as client:
        response = await client.post(build_image_url(OPENAI_IMAGE_EDIT_PATH), headers=headers, data=data, files=files)
        return await parse_image_response(response)


async def run_openai_image_agent(
    prompt: str,
    record_text: str | None,
    uploaded_image: Any | None,
    session_scope: str | None = None,
) -> AgentReply:
    """调用 OpenAI Images API；有上传图时编辑图片，否则从文本生成图片。"""

    image_prompt = extract_user_image_prompt(prompt, record_text)

    try:
        async with acquire_runtime_lock(session_scope, "openai-image"):
            if uploaded_image is not None:
                payload = await call_image_edit_api(image_prompt, uploaded_image)
            else:
                payload = await call_image_generate_api(image_prompt)
    except OpenAIImageRequestError:
        raise
    except httpx.TimeoutException as exc:
        raise OpenAIImageRequestError("图片接口请求超时：服务商响应太慢，可以稍后重试或降低图片尺寸。") from exc
    except Exception:
        logger.exception("OpenAI image request failed")
        raise

    images = await extract_generated_images_from_payload(payload)
    if not images:
        raise RuntimeError("OpenAI image service returned no image data.")

    append_conversation_record(record_text or prompt, "[生成了一张图片]", None, session_scope)
    return AgentReply(text="", images=images)


async def run_openai_agent(
    prompt: str,
    sender: MessageSender,
    target_id: str | int,
    continue_session: bool,
    record_text: str | None = None,
    uploaded_image: Any | None = None,
    uploaded_file: Any | None = None,
    session_scope: str | None = None,
    channel: str | None = None,
    decision_plan: DecisionPlan | None = None,
) -> AgentReply:
    """OpenAI Provider：使用 chat/completions + SSE + 最小工具集。"""

    if wants_image_output(prompt, record_text, uploaded_image):
        return await run_openai_image_agent(prompt, record_text, uploaded_image, session_scope)

    headers = build_chat_headers()
    messages = [
        {"role": "system", "content": build_openai_instructions(prompt, session_scope, decision_plan)},
        *build_chat_messages(prompt, uploaded_image),
    ]
    tool_ctx = OpenAIToolContext(
        sender=sender,
        target_id=target_id,
        channel=channel,
        session_scope=session_scope,
        enforce_confirmations=hasattr(sender, "confirm_tool_use"),
    )
    tools = build_openai_tools(tool_ctx)

    try:
        async with acquire_runtime_lock(session_scope, "openai"):
            async with httpx.AsyncClient(timeout=90) as client:
                final_text = ""
                last_stream_preview: list[str] = []
                for _ in range(3):
                    payload = {
                        "model": get_effective_model("openai"),
                        "messages": messages,
                        "stream": True,
                        "tools": tools,
                        "tool_choice": "auto",
                    }
                    if len(messages) == 2 and messages[-1].get("role") == "user":
                        payload["temperature"] = 0.7

                    stream_result = await stream_chat_completion(
                        client,
                        headers,
                        payload,
                        timeout_hint="primary",
                    )
                    last_stream_preview = stream_result.raw_preview

                    if stream_result.tool_calls:
                        messages.append(
                            {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": call.id,
                                        "type": "function",
                                        "function": {"name": call.name, "arguments": call.arguments},
                                    }
                                    for call in stream_result.tool_calls
                                ],
                            }
                        )

                        for call in stream_result.tool_calls:
                            try:
                                arguments = json.loads(call.arguments) if call.arguments else {}
                            except json.JSONDecodeError:
                                arguments = {}
                            tool_output = await execute_openai_tool(call.name, arguments, tool_ctx)
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call.id,
                                    "content": tool_output,
                                }
                            )
                        continue

                    final_text = stream_result.text.strip()
                    break

                if not final_text:
                    # 某些中转在带 tools 时会返回空文本，但不报错。
                    # 这里退化成纯文本 chat/completions 再试一次，优先保证基础问答可用。
                    fallback_payload = {
                        "model": get_effective_model("openai"),
                        "messages": messages,
                        "stream": True,
                        "temperature": 0.7,
                    }
                    fallback_result = await stream_chat_completion(
                        client,
                        headers,
                        fallback_payload,
                        timeout_hint="fallback",
                    )
                    last_stream_preview = fallback_result.raw_preview
                    final_text = fallback_result.text.strip()

                if not final_text:
                    logger.warning("OpenAI empty response preview: %s", last_stream_preview)
    except Exception:
        logger.exception("OpenAI request failed")
        raise

    if not final_text:
        raise RuntimeError("OpenAI service returned an empty response.")

    append_conversation_record(record_text or prompt, final_text, None if not continue_session else "openai", session_scope)
    return AgentReply.from_text(final_text)
