from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass(slots=True)
class OpenAIToolCall:
    id: str
    name: str
    arguments: str


@dataclass(slots=True)
class OpenAIChatStreamResult:
    text: str = ""
    tool_calls: list[OpenAIToolCall] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    chunk_count: int = 0
    raw_preview: list[str] = field(default_factory=list)


def extract_text_from_content_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""

    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("content") or item.get("value") or ""
        if isinstance(text, str) and text:
            parts.append(text)
    return "".join(parts)


def extract_chat_chunk_text(chunk: dict[str, Any]) -> str:
    choices = chunk.get("choices", [])
    if not choices:
        return ""

    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta", {}) if isinstance(choice.get("delta", {}), dict) else {}
    message = choice.get("message", {}) if isinstance(choice.get("message", {}), dict) else {}

    for candidate in (
        delta.get("content"),
        message.get("content"),
        choice.get("text"),
    ):
        text = extract_text_from_content_value(candidate)
        if text:
            return text
    return ""


def _accumulate_tool_calls(tool_calls_by_index: dict[int, dict[str, str]], chunk: dict[str, Any]) -> None:
    choices = chunk.get("choices", [])
    if not choices:
        return
    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta", {}) if isinstance(choice.get("delta", {}), dict) else {}

    for item in delta.get("tool_calls", []) or []:
        if not isinstance(item, dict):
            continue
        idx = int(item.get("index", 0))
        current = tool_calls_by_index.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if item.get("id"):
            current["id"] = item["id"]
        function_block = item.get("function", {}) if isinstance(item.get("function", {}), dict) else {}
        if function_block.get("name"):
            current["name"] = function_block["name"]
        if function_block.get("arguments"):
            current["arguments"] += function_block["arguments"]


async def collect_chat_sse_response(response: httpx.Response) -> OpenAIChatStreamResult:
    result = OpenAIChatStreamResult()
    tool_calls_by_index: dict[int, dict[str, str]] = {}

    async for raw_line in response.aiter_lines():
        line = raw_line.strip()
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if len(result.raw_preview) < 5:
            result.raw_preview.append(data_str[:300])
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        result.chunk_count += 1
        text = extract_chat_chunk_text(chunk)
        if text:
            result.text += text
        if chunk.get("usage"):
            result.usage = chunk["usage"]

        _accumulate_tool_calls(tool_calls_by_index, chunk)

    for index in sorted(tool_calls_by_index):
        call = tool_calls_by_index[index]
        result.tool_calls.append(
            OpenAIToolCall(
                id=call.get("id", ""),
                name=call.get("name", ""),
                arguments=call.get("arguments", "") or "{}",
            )
        )
    return result
