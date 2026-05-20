from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from weclaw.config import OPENAI_CHAT_TIMEOUT_SECONDS, WORKSPACE_DIR
from weclaw.core.provider_model import get_effective_api_key, get_effective_base_url, get_effective_model
from weclaw.core.provider_state import get_provider, normalize_provider

logger = logging.getLogger(__name__)


REFLECTION_SYSTEM_PROMPT = (
    "You are WeClaw's memory reflection worker. "
    "Identify durable user preferences, rules that should be rewritten, candidates that should be promoted, "
    "and stale slots that should be archived. Return JSON only."
)


@dataclass(slots=True)
class ReflectionModelResult:
    text: str = ""
    provider: str = ""
    model: str = ""
    used_model: bool = False
    errors: list[str] = field(default_factory=list)


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


async def _run_openai_reflection(prompt: str, api_key_override: str | None = None) -> ReflectionModelResult:
    provider = "openai"
    model = get_effective_model(provider)
    api_key = api_key_override if api_key_override is not None else get_effective_api_key(provider)
    if not api_key:
        return ReflectionModelResult(provider=provider, model=model, errors=["OPENAI_API_KEY is not configured."])

    base_url = get_effective_base_url(provider).rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    timeout = httpx.Timeout(max(float(OPENAI_CHAT_TIMEOUT_SECONDS), 30.0), connect=30.0, write=30.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
    if response.status_code != 200:
        return ReflectionModelResult(
            provider=provider,
            model=model,
            used_model=True,
            errors=[f"HTTP {response.status_code}: {response.text[:500]}"],
        )

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        return ReflectionModelResult(provider=provider, model=model, used_model=True, errors=[f"Invalid JSON response: {exc}"])
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        return ReflectionModelResult(provider=provider, model=model, used_model=True, errors=["No choices returned."])
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    text = _extract_text(message.get("content")).strip()
    if not text:
        return ReflectionModelResult(provider=provider, model=model, used_model=True, errors=["Empty content returned."])
    return ReflectionModelResult(text=text, provider=provider, model=model, used_model=True)


async def _run_claude_reflection(prompt: str, api_key_override: str | None = None) -> ReflectionModelResult:
    provider = "claude"
    model = get_effective_model(provider)
    api_key = api_key_override if api_key_override is not None else get_effective_api_key(provider)
    if not api_key:
        return ReflectionModelResult(provider=provider, model=model, errors=["ANTHROPIC_API_KEY is not configured."])

    try:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query
    except Exception as exc:
        return ReflectionModelResult(provider=provider, model=model, errors=[f"Claude SDK unavailable: {exc}"])

    env = {"ANTHROPIC_API_KEY": api_key}
    base_url = get_effective_base_url(provider)
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    if model:
        env["ANTHROPIC_MODEL"] = model

    options = ClaudeAgentOptions(
        permission_mode="acceptEdits",
        env=env,
        cwd=str(WORKSPACE_DIR),
        tools=[],
        allowed_tools=[],
        system_prompt=REFLECTION_SYSTEM_PROMPT,
    )
    text_parts: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage) and message.result:
            text_parts.append(message.result)
    text = "\n".join(part for part in text_parts if part).strip()
    if not text:
        return ReflectionModelResult(provider=provider, model=model, used_model=True, errors=["Empty content returned."])
    return ReflectionModelResult(text=text, provider=provider, model=model, used_model=True)


async def run_reflection_model(prompt: str, provider_override: str | None = None, api_key_override: str | None = None) -> ReflectionModelResult:
    provider = normalize_provider(provider_override or get_provider(), default="openai")
    try:
        if provider == "claude":
            return await _run_claude_reflection(prompt, api_key_override=api_key_override)
        if provider == "openai":
            return await _run_openai_reflection(prompt, api_key_override=api_key_override)
        return ReflectionModelResult(provider=provider, model="", errors=[f"Unsupported provider: {provider}"])
    except Exception as exc:
        logger.exception("Memory reflection model call failed: %s", provider)
        return ReflectionModelResult(provider=provider, model=get_effective_model(provider), used_model=True, errors=[str(exc)])
