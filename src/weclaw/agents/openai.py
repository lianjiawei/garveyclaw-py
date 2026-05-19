from __future__ import annotations



import base64

import io

import json

import logging

import re

from typing import Any

from urllib.parse import urljoin



import httpx



from weclaw.agents.prompts import build_openai_instructions as build_shared_openai_instructions

from weclaw.core.response import AgentImage, AgentReply

from weclaw.decision.models import DecisionPlan

from weclaw.config import (

    OPENAI_API_KEY,

    OPENAI_BASE_URL,

    OPENAI_CHAT_DISABLE_REASONING,

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
    OPENAI_CONVERSATION_HISTORY_MAX_CHARS,
    OPENAI_CONVERSATION_HISTORY_TURNS,
    OPENAI_CHAT_TIMEOUT_SECONDS,
    OPENAI_TOOL_CALL_MAX_ROUNDS,
)
from weclaw.core.delivery import MessageSender
from weclaw.core.provider_model import get_effective_api_key, get_effective_base_url, get_effective_model
from weclaw.memory.store import append_conversation_record, load_recent_conversation_turns

from weclaw.core.locks import acquire_runtime_lock

from weclaw.agents.openai_stream import collect_chat_sse_response
from weclaw.agents.openai_stream import extract_text_from_content_value

from weclaw.agents.openai_tools import OpenAIToolContext, build_openai_tools, execute_openai_tool



logger = logging.getLogger(__name__)


def _should_disable_reasoning() -> bool:
    setting = (OPENAI_CHAT_DISABLE_REASONING or "auto").strip().lower()
    if setting in {"1", "true", "yes", "on"}:
        return True
    if setting in {"0", "false", "no", "off"}:
        return False
    model = (get_effective_model("openai") or "").lower()
    base_url = (get_effective_base_url("openai") or "").lower()
    return model.startswith("qwen") or "dashscope" in base_url


def _apply_openai_compat_options(payload: dict[str, Any]) -> dict[str, Any]:
    if _should_disable_reasoning():
        payload.setdefault("enable_thinking", False)
    return payload


def _without_provider_options(payload: dict[str, Any]) -> dict[str, Any]:
    fallback = dict(payload)
    fallback.pop("enable_thinking", None)
    return fallback


def _chat_timeout() -> httpx.Timeout:
    timeout = max(float(OPENAI_CHAT_TIMEOUT_SECONDS), 30.0)
    return httpx.Timeout(timeout=timeout, connect=30.0, write=30.0, pool=30.0)



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
            "Run `weclaw setup`, or add a provider with `weclaw model add --protocol openai ...`."
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





def build_openai_instructions(
    prompt: str,
    session_scope: str | None = None,
    decision_plan: DecisionPlan | None = None,
    *,
    has_uploaded_image: bool = False,
) -> str:

    """构造 OpenAI chat/completions 使用的系统提示。"""

    return build_shared_openai_instructions(prompt, session_scope, decision_plan, has_uploaded_image=has_uploaded_image)





def build_history_messages(history: list[dict[str, str]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for turn in history:
        user_message = str(turn.get("user") or "").strip()
        assistant_reply = str(turn.get("assistant") or "").strip()
        if not user_message or not assistant_reply:
            continue
        messages.append({"role": "user", "content": user_message})
        messages.append({"role": "assistant", "content": assistant_reply})
    return messages


def build_chat_messages(prompt: str, uploaded_image: Any | None, history: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:

    messages = build_history_messages(history or [])

    if uploaded_image is None:

        return [*messages, {"role": "user", "content": prompt}]



    image_data = base64.b64encode(uploaded_image.data).decode("ascii")

    return [

        *messages,

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
            "Run `weclaw setup`, or add one with `weclaw model add --protocol openai ...`."
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
            error_preview = error_text.decode("utf-8", errors="replace")[:500]
            if "enable_thinking" in payload and response.status_code in {400, 422}:
                logger.info("OpenAI %s rejected enable_thinking; retrying without provider-specific option.", timeout_hint)
                return await stream_chat_completion(
                    client,
                    headers,
                    _without_provider_options(payload),
                    timeout_hint=timeout_hint,
                )

            raise RuntimeError(

                f"OpenAI {timeout_hint} chat/completions failed: HTTP {response.status_code} - "

                f"{error_preview}"

            )

        return await collect_chat_sse_response(response)


async def call_chat_completion(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    timeout_hint: str,
) -> str:
    response = await client.post(
        f"{get_effective_base_url('openai').rstrip('/')}/chat/completions",
        headers=headers,
        json=payload,
    )
    if response.status_code != 200:
        if "enable_thinking" in payload and response.status_code in {400, 422}:
            logger.info("OpenAI %s rejected enable_thinking; retrying without provider-specific option.", timeout_hint)
            return await call_chat_completion(
                client,
                headers,
                _without_provider_options(payload),
                timeout_hint=timeout_hint,
            )
        raise RuntimeError(
            f"OpenAI {timeout_hint} chat/completions failed: HTTP {response.status_code} - "
            f"{response.text[:500]}"
        )
    payload = response.json()
    choices = payload.get("choices", [])
    if not choices:
        return ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message", {}) if isinstance(choice.get("message", {}), dict) else {}
    content = extract_text_from_content_value(message.get("content")).strip()
    if content:
        return content
    reasoning = extract_text_from_content_value(message.get("reasoning_content") or message.get("reasoning")).strip()
    if reasoning:
        logger.info("OpenAI non-stream response contained reasoning only: chars=%s", len(reasoning))
    return ""





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
    history = (
        load_recent_conversation_turns(
            session_scope,
            limit=OPENAI_CONVERSATION_HISTORY_TURNS,
            max_chars_per_message=OPENAI_CONVERSATION_HISTORY_MAX_CHARS,
        )
        if continue_session
        else []
    )

    messages = [

        {"role": "system", "content": build_openai_instructions(prompt, session_scope, decision_plan, has_uploaded_image=uploaded_image is not None)},

        *build_chat_messages(prompt, uploaded_image, history),

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

            async with httpx.AsyncClient(timeout=_chat_timeout()) as client:

                final_text = ""

                last_stream_preview: list[str] = []
                reasoning_only_seen = False
                timeout_seen = False
                tool_round_limit_hit = False
                max_tool_rounds = max(1, int(OPENAI_TOOL_CALL_MAX_ROUNDS))

                for tool_round in range(max_tool_rounds):

                    payload = _apply_openai_compat_options({
                        "model": get_effective_model("openai"),
                        "messages": messages,
                        "stream": True,
                        "tools": tools,
                        "tool_choice": "auto",

                    })

                    if len(messages) == 2 and messages[-1].get("role") == "user":

                        payload["temperature"] = 0.7



                    try:
                        stream_result = await stream_chat_completion(

                            client,

                            headers,

                            payload,

                            timeout_hint="primary",

                        )
                    except httpx.ReadTimeout:
                        timeout_seen = True
                        logger.warning("OpenAI primary stream timed out; falling back to plain chat completion.")
                        break

                    last_stream_preview = stream_result.raw_preview
                    if not stream_result.text and stream_result.reasoning_chunk_count:
                        reasoning_only_seen = True
                        logger.info(
                            "OpenAI stream returned reasoning-only chunks: phase=primary chunks=%s reasoning_chunks=%s reasoning_preview=%r",
                            stream_result.chunk_count,
                            stream_result.reasoning_chunk_count,
                            stream_result.reasoning_text[:160],
                        )



                    if stream_result.tool_calls:
                        if tool_round + 1 >= max_tool_rounds:
                            tool_round_limit_hit = True

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

                        if not tool_round_limit_hit:
                            continue
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "工具调用轮次已达到上限。请停止继续调用工具，直接基于上面已经返回的工具结果，"
                                    "给用户一个清晰、可执行的最终答复；如果信息仍不足，请明确说明缺口。"
                                ),
                            }
                        )
                        break



                    final_text = stream_result.text.strip()

                    break



                if not final_text:

                    # 某些中转在带 tools 时会返回空文本，但不报错。

                    # 这里退化成纯文本 chat/completions 再试一次，优先保证基础问答可用。

                    fallback_payload = _apply_openai_compat_options({
                        "model": get_effective_model("openai"),
                        "messages": messages,
                        "stream": True,
                        "temperature": 0.7,
                    })

                    try:
                        fallback_result = await stream_chat_completion(

                            client,

                            headers,

                            fallback_payload,

                            timeout_hint="fallback",

                        )
                    except httpx.ReadTimeout:
                        timeout_seen = True
                        logger.warning("OpenAI fallback stream timed out; trying non-stream fallback.")
                        fallback_result = None

                    if fallback_result is not None:
                        last_stream_preview = fallback_result.raw_preview
                        if fallback_result.tool_calls:
                            tool_round_limit_hit = True
                            logger.info(
                                "OpenAI fallback returned tool calls after tool round limit: calls=%s",
                                [call.name for call in fallback_result.tool_calls],
                            )
                        if not fallback_result.text and fallback_result.reasoning_chunk_count:
                            reasoning_only_seen = True
                            logger.info(
                                "OpenAI stream returned reasoning-only chunks: phase=fallback chunks=%s reasoning_chunks=%s reasoning_preview=%r",
                                fallback_result.chunk_count,
                                fallback_result.reasoning_chunk_count,
                                fallback_result.reasoning_text[:160],
                            )

                        final_text = fallback_result.text.strip()


                if not final_text:

                    # Some OpenAI-compatible reasoning models stream reasoning_content
                    # without content. Non-streaming replies are often more stable for
                    # the final assistant message, especially after image turns.
                    non_stream_payload = _apply_openai_compat_options({
                        "model": get_effective_model("openai"),
                        "messages": messages,
                        "stream": False,
                        "temperature": 0.7,
                    })

                    try:
                        final_text = await call_chat_completion(

                            client,

                            headers,

                            non_stream_payload,

                            timeout_hint="non-stream fallback",

                        )
                    except httpx.ReadTimeout:
                        timeout_seen = True
                        logger.warning("OpenAI non-stream fallback timed out.")



                if not final_text:
                    if reasoning_only_seen:
                        final_text = (
                            "模型服务这次只返回了 reasoning_content，没有返回最终答复内容。"
                            "我已经避免把推理过程直接展示出来。请重试一次，或将 OPENAI_CHAT_DISABLE_REASONING=1 后重启；"
                            "如果仍复现，建议把当前 qwen reasoning 模型切到非 thinking 模式/非 reasoning 模型。"
                        )
                    elif timeout_seen:
                        final_text = (
                            "模型服务这次响应超时，工具授权和执行流程已经开始，但模型没有在超时时间内返回最终答复。"
                            "请稍后重试，或把任务拆小一点；如果经常发生，可以调大 OPENAI_CHAT_TIMEOUT_SECONDS 后重启。"
                        )
                    elif tool_round_limit_hit:
                        final_text = (
                            "模型连续请求调用工具，已达到本次会话的工具调用轮次上限。"
                            "我已停止继续执行更多工具，避免任务陷入循环。请把目标拆小一点再试，"
                            "或调大 OPENAI_TOOL_CALL_MAX_ROUNDS 后重启。"
                        )
                    else:

                        logger.warning("OpenAI empty response preview: %s", last_stream_preview)

    except Exception:

        logger.exception("OpenAI request failed")

        raise



    if not final_text:

        raise RuntimeError("OpenAI service returned an empty response.")



    append_conversation_record(record_text or prompt, final_text, None if not continue_session else "openai", session_scope)

    return AgentReply.from_text(final_text)
