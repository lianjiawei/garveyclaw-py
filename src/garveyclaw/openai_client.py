from __future__ import annotations

import base64
import io
import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from garveyclaw.agent_response import AgentImage, AgentReply
from garveyclaw.claude_client import AGENT_LOCK, build_system_prompt
from garveyclaw.config import (
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
    OPENAI_MODEL,
)
from garveyclaw.memory_store import append_conversation_record

logger = logging.getLogger(__name__)

IMAGE_REQUEST_KEYWORDS = (
    "生成图片",
    "生成一张",
    "画一张",
    "做一张图",
    "做图",
    "生图",
    "改图",
    "改成",
    "编辑图片",
    "修改图片",
    "变成",
    "换成",
    "风格",
    "头像",
    "海报",
    "插画",
    "image",
    "draw",
    "generate",
)


class OpenAIImageRequestError(RuntimeError):
    """图片生成/编辑接口失败时，给 Telegram 展示更可读的错误原因。"""


def build_openai_client():
    """延迟创建 OpenAI client，避免 Claude 模式也强依赖 openai 包。"""

    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK is not installed. Run: python -m pip install -e .") from exc

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    kwargs: dict[str, Any] = {"api_key": OPENAI_API_KEY}
    if OPENAI_BASE_URL:
        kwargs["base_url"] = OPENAI_BASE_URL
    return AsyncOpenAI(**kwargs)


def get_image_api_key() -> str:
    """图片接口可以单独配置 key；不配置时复用文本 OpenAI key。"""

    api_key = OPENAI_IMAGE_API_KEY or OPENAI_API_KEY
    if not api_key:
        raise RuntimeError("OPENAI_IMAGE_API_KEY or OPENAI_API_KEY is not configured.")
    return api_key


def build_image_url(path: str) -> str:
    """构造图片接口地址，兼容服务商自定义路径。"""

    base_url = OPENAI_IMAGE_BASE_URL or OPENAI_BASE_URL
    if not base_url:
        raise RuntimeError("OPENAI_IMAGE_BASE_URL or OPENAI_BASE_URL is not configured.")
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

    user_prompt = extract_user_image_prompt(prompt, record_text).lower()
    if any(keyword.lower() in user_prompt for keyword in IMAGE_REQUEST_KEYWORDS):
        return True

    return False


def build_openai_input(prompt: str, uploaded_image: Any | None) -> list[dict[str, Any]]:
    """构造 Responses API 输入；图片使用 data URL，避免本地落盘。"""

    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]

    if uploaded_image is not None:
        image_data = base64.b64encode(uploaded_image.data).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{uploaded_image.mime_type};base64,{image_data}",
            }
        )

    return [{"role": "user", "content": content}]


def build_openai_instructions(prompt: str) -> str:
    """复用项目记忆和 skill 上下文，并声明 OpenAI 第一版不接 Claude Code 工具。"""

    return (
        build_system_prompt(prompt)
        + "\n\n当前使用的是 OpenAI Provider 第一版。"
        + "本模式支持文本、图片理解和图片生成/编辑，但不直接提供 Claude Code 内置工具、MCP 工具、"
        + "文件读写、Bash、WebSearch 或主动发送 Telegram 消息工具。"
        + "如果用户请求这些工具能力，请说明当前 Provider 的能力边界，"
        + "并建议切换 AGENT_PROVIDER=claude。"
    )


def extract_response_text(response: Any) -> str:
    """兼容不同 OpenAI SDK 版本的文本读取方式。"""

    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                parts.append(text)
    return "\n".join(parts)


def extract_generated_images(response: Any) -> list[AgentImage]:
    """从 Images API 返回里提取 base64 图片，保持内存传递给 Telegram。"""

    images: list[AgentImage] = []
    for item in getattr(response, "data", []) or []:
        b64_json = getattr(item, "b64_json", None)
        if not b64_json:
            continue
        images.append(
            AgentImage(
                data=base64.b64decode(b64_json),
                mime_type=f"image/{OPENAI_IMAGE_OUTPUT_FORMAT}",
            )
        )
    return images


def extract_generated_images_from_payload(payload: dict[str, Any]) -> list[AgentImage]:
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


async def run_openai_image_agent(prompt: str, record_text: str | None, uploaded_image: Any | None) -> AgentReply:
    """调用 OpenAI Images API；有上传图时编辑图片，否则从文本生成图片。"""

    image_prompt = extract_user_image_prompt(prompt, record_text)

    try:
        async with AGENT_LOCK:
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

    images = extract_generated_images_from_payload(payload)
    if not images:
        raise RuntimeError("OpenAI image service returned no image data.")

    text = "图片已生成。"
    append_conversation_record(record_text or prompt, text, None)
    return AgentReply(text=text, images=images)


async def run_openai_agent(
    prompt: str,
    bot,
    chat_id: int,
    continue_session: bool,
    record_text: str | None = None,
    uploaded_image: Any | None = None,
) -> AgentReply:
    """第一版 OpenAI Provider：支持文本、图片理解和图片生成/编辑。"""

    if wants_image_output(prompt, record_text, uploaded_image):
        return await run_openai_image_agent(prompt, record_text, uploaded_image)

    client = build_openai_client()
    request_input = build_openai_input(prompt, uploaded_image)

    try:
        async with AGENT_LOCK:
            response = await client.responses.create(
                model=OPENAI_MODEL,
                instructions=build_openai_instructions(prompt),
                input=request_input,
            )
    except Exception:
        logger.exception("OpenAI request failed")
        raise

    text = extract_response_text(response)
    if not text.strip():
        raise RuntimeError("OpenAI service returned an empty response.")

    append_conversation_record(record_text or prompt, text, None if not continue_session else "openai")
    return AgentReply.from_text(text)
