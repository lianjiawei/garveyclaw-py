from __future__ import annotations

import logging
from typing import Any

from telegram import Update

from garveyclaw.config import AGENT_PROVIDER
from garveyclaw.agent_response import AgentReply

logger = logging.getLogger(__name__)


class AgentServiceError(Exception):
    """统一表示当前 Agent Provider 调用失败。"""


def normalize_provider_name() -> str:
    return AGENT_PROVIDER.strip().lower()


async def ask_agent(
    prompt: str,
    update: Update,
    record_text: str | None = None,
    uploaded_image: Any | None = None,
) -> AgentReply:
    """Telegram 消息统一入口，根据配置选择具体模型 Provider。"""

    if not update.effective_chat:
        raise AgentServiceError("Missing Telegram chat context.")

    return await run_agent(
        prompt=prompt,
        bot=update.get_bot(),
        chat_id=update.effective_chat.id,
        continue_session=True,
        record_text=record_text,
        uploaded_image=uploaded_image,
    )


async def run_agent(
    prompt: str,
    bot,
    chat_id: int,
    continue_session: bool,
    record_text: str | None = None,
    uploaded_image: Any | None = None,
) -> AgentReply:
    """统一 Agent 调用入口，后续可以继续扩展更多 Provider。"""

    provider = normalize_provider_name()

    try:
        if provider == "claude":
            from garveyclaw.claude_client import run_agent as run_claude_agent

            text = await run_claude_agent(
                prompt=prompt,
                bot=bot,
                chat_id=chat_id,
                continue_session=continue_session,
                record_text=record_text,
                uploaded_image=uploaded_image,
            )
            return AgentReply.from_text(text)

        if provider == "openai":
            from garveyclaw.openai_client import run_openai_agent

            return await run_openai_agent(
                prompt=prompt,
                bot=bot,
                chat_id=chat_id,
                continue_session=continue_session,
                record_text=record_text,
                uploaded_image=uploaded_image,
            )

        raise AgentServiceError(f"Unsupported AGENT_PROVIDER: {AGENT_PROVIDER}")
    except AgentServiceError:
        raise
    except Exception as exc:
        logger.exception("Agent provider request failed: %s", provider)
        raise AgentServiceError(str(exc) or "Failed to get response from agent provider.") from exc
