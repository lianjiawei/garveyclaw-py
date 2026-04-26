from __future__ import annotations

import asyncio
import logging
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    query,
)
from telegram import Update

from garveyclaw.agent_tools import build_mcp_server
from garveyclaw.config import (
    ALLOWED_TOOLS,
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    ANTHROPIC_MODEL,
    CLAUDE_TOOLS_PRESET,
    SHOW_TOOL_TRACE,
    WORKSPACE_DIR,
)
from garveyclaw.memory_store import append_conversation_record, load_long_term_memory
from garveyclaw.session_store import load_session_id, save_session_id
from garveyclaw.skill_store import build_skill_prompt

logger = logging.getLogger(__name__)

# 所有 Agent 调用共用一把锁，避免普通消息和定时任务同时并发执行。
AGENT_LOCK = asyncio.Lock()


class ClaudeServiceError(Exception):
    """统一表示模型调用失败。"""


def build_system_prompt(prompt: str) -> str:
    """构造当前 Agent 调用使用的 system prompt。"""

    long_term_memory = load_long_term_memory()
    selected_skills, skill_prompt = build_skill_prompt(prompt)
    selected_skill_names = ", ".join(skill.name for skill in selected_skills) or "无"

    return f"""
你现在运行在一个 Telegram 机器人中。
当前工作区目录是：{WORKSPACE_DIR}

下面是从 CLAUDE.md 读取到的长期记忆：
{long_term_memory}

本轮命中的 skill：{selected_skill_names}

{skill_prompt}

规则：
1. 当用户询问文件、目录或当前时间时，优先使用工具。
2. 如果需要额外主动给 Telegram 发送一条消息，请使用 send_message 工具。
3. 不要编造文件内容；如果需要文件数据，就调用工具读取。
4. 如果使用 Bash，请优先选择当前环境更稳妥的命令。
5. 当前环境里不要默认使用 `python3`，优先尝试 `python`。
6. 当前环境不保证安装了 `gh` 等额外命令行工具，不要默认依赖它们。
7. 如果 WebSearch 或 WebFetch 不可用，可以再考虑使用 Bash + 通用网络命令作为备选方案。
8. 回答尽量使用自然、清晰的中文。
""".strip()


def build_tool_hooks(bot, chat_id: int) -> dict[str, list[HookMatcher]]:
    """构造工具执行过程的 Telegram 状态通知。"""

    async def notify_tool_start(hook_input, tool_use_id, context) -> dict:
        await bot.send_message(chat_id=chat_id, text=f"[Tool Start] {hook_input['tool_name']}")
        return {}

    async def notify_tool_finish(hook_input, tool_use_id, context) -> dict:
        await bot.send_message(chat_id=chat_id, text=f"[Tool Done] {hook_input['tool_name']}")
        return {}

    async def notify_tool_failure(hook_input, tool_use_id, context) -> dict:
        await bot.send_message(chat_id=chat_id, text=f"[Tool Failed] {hook_input['tool_name']}: {hook_input['error']}")
        return {}

    return {
        "PreToolUse": [HookMatcher(hooks=[notify_tool_start])],
        "PostToolUse": [HookMatcher(hooks=[notify_tool_finish])],
        "PostToolUseFailure": [HookMatcher(hooks=[notify_tool_failure])],
    }


async def ask_claude(
    prompt: str,
    update: Update,
    record_text: str | None = None,
    uploaded_image: Any | None = None,
) -> str:
    """处理来自 Telegram 的普通消息调用。"""

    if not update.effective_chat:
        raise ClaudeServiceError("Missing Telegram chat context.")

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
) -> str:
    """运行一次 Claude Agent，并负责 session 与对话记录落盘。"""

    env = {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "ANTHROPIC_BASE_URL": ANTHROPIC_BASE_URL,
        "ANTHROPIC_MODEL": ANTHROPIC_MODEL,
    }

    tool_server = build_mcp_server(bot=bot, chat_id=chat_id, uploaded_image=uploaded_image)
    saved_session_id = load_session_id() if continue_session else None
    options = ClaudeAgentOptions(
        permission_mode="acceptEdits",
        env=env,
        cwd=str(WORKSPACE_DIR),
        tools=CLAUDE_TOOLS_PRESET,
        system_prompt=build_system_prompt(prompt),
        mcp_servers={"garveyclaw": tool_server},
        allowed_tools=ALLOWED_TOOLS,
        hooks=build_tool_hooks(bot, chat_id) if SHOW_TOOL_TRACE else {},
        continue_conversation=continue_session and bool(saved_session_id),
        resume=saved_session_id,
    )

    final_result = None
    text_parts: list[str] = []
    latest_session_id: str | None = None

    try:
        async with AGENT_LOCK:
            async for message in query(prompt=prompt, options=options):
                if getattr(message, "session_id", None):
                    latest_session_id = message.session_id
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                elif isinstance(message, ResultMessage) and message.result:
                    final_result = message.result
    except Exception as exc:
        logger.exception("Claude request failed")
        raise ClaudeServiceError("Failed to get response from Claude service.") from exc

    response = final_result or "\n".join(text_parts)
    if not response.strip():
        raise ClaudeServiceError("Claude service returned an empty response.")

    if latest_session_id:
        save_session_id(latest_session_id)

    append_conversation_record(record_text or prompt, response, latest_session_id if continue_session else None)
    return response
