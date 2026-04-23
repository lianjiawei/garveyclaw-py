import asyncio
import logging

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
    WORKSPACE_DIR,
)
from garveyclaw.memory_store import append_conversation_record, load_long_term_memory
from garveyclaw.session_store import load_session_id, save_session_id

logger = logging.getLogger(__name__)

# 所有 Agent 调用共用一把锁，避免普通消息和定时任务并发执行。
AGENT_LOCK = asyncio.Lock()


class ClaudeServiceError(Exception):
    """统一表示模型调用失败。"""


def build_system_prompt() -> str:
    # 给模型补充当前运行环境、长期记忆和工具使用约束。
    long_term_memory = load_long_term_memory()
    return f"""
你现在运行在一个 Telegram 机器人中。

当前工作区目录是：
{WORKSPACE_DIR}

下面是从 CLAUDE.md 读取到的长期记忆：
{long_term_memory}

规则：
1. 当用户询问文件、目录或当前时间时，优先使用工具。
2. 如果需要额外主动给 Telegram 发送一条消息，请使用 send_message 工具。
3. 不要编造文件内容；如果需要文件数据，就调用工具读取。
4. 回答尽量使用自然、清晰的中文。
""".strip()


def build_tool_hooks(bot, chat_id: int) -> dict[str, list[HookMatcher]]:
    async def notify_tool_start(hook_input, tool_use_id, context) -> dict:
        # 工具开始前先发一条状态消息，方便在 Telegram 里观察执行过程。
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


async def ask_claude(prompt: str, update: Update) -> str:
    if not update.effective_chat:
        raise ClaudeServiceError("Missing Telegram chat context.")

    return await run_agent(
        prompt=prompt,
        bot=update.get_bot(),
        chat_id=update.effective_chat.id,
        continue_session=True,
    )


async def run_agent(prompt: str, bot, chat_id: int, continue_session: bool) -> str:
    # 显式传入模型调用配置，避免运行时依赖外部隐式环境。
    env = {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "ANTHROPIC_BASE_URL": ANTHROPIC_BASE_URL,
        "ANTHROPIC_MODEL": ANTHROPIC_MODEL,
    }

    tool_server = build_mcp_server(bot=bot, chat_id=chat_id)
    saved_session_id = load_session_id() if continue_session else None
    options = ClaudeAgentOptions(
        permission_mode="acceptEdits",
        env=env,
        cwd=str(WORKSPACE_DIR),
        tools=CLAUDE_TOOLS_PRESET,
        system_prompt=build_system_prompt(),
        mcp_servers={"garveyclaw": tool_server},
        allowed_tools=ALLOWED_TOOLS,
        hooks=build_tool_hooks(bot, chat_id),
        continue_conversation=continue_session and bool(saved_session_id),
        resume=saved_session_id,
    )

    final_result = None
    text_parts: list[str] = []
    latest_session_id: str | None = None

    try:
        async with AGENT_LOCK:
            async for message in query(prompt=prompt, options=options):
                # 同时兼容中间文本块和最终结果消息。
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

    append_conversation_record(prompt, response, latest_session_id if continue_session else None)

    return response
