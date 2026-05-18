from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    query,
)

from hiclaw.capabilities.tools import ToolContext, build_claude_allowed_tools
from hiclaw.agents.tools import build_mcp_server
from hiclaw.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    ANTHROPIC_MODEL,
    SHOW_TOOL_TRACE,
    WORKSPACE_DIR,
)
from hiclaw.core.delivery import MessageSender, send_sender_text
from hiclaw.core.agent_activity import mark_agent_tool_finished, mark_agent_tool_started
from hiclaw.core.provider_model import get_effective_api_key, get_effective_base_url, get_effective_model
from hiclaw.decision.models import DecisionPlan
from hiclaw.decision.render import render_decision_plan
from hiclaw.memory.store import append_conversation_record, build_context_snapshot
from hiclaw.core.locks import acquire_runtime_lock
from hiclaw.core.types import ConversationRef
from hiclaw.memory.session import load_session_id, save_session_id
from hiclaw.skills.store import build_skill_prompt

if TYPE_CHECKING:
    from telegram import Update

logger = logging.getLogger(__name__)

PROMPTS_DIR = WORKSPACE_DIR / "prompts"
CLAUDE_BASE_ALLOWED_TOOLS: list[str] = []
_DEFAULT_ANTHROPIC_API_KEY = ANTHROPIC_API_KEY
_DEFAULT_ANTHROPIC_BASE_URL = ANTHROPIC_BASE_URL
_DEFAULT_ANTHROPIC_MODEL = ANTHROPIC_MODEL


class ClaudeServiceError(Exception):
    """统一表示模型调用失败。"""


class ClaudeConfigurationError(ClaudeServiceError):
    """表示 Claude Provider 缺少必要配置。"""


def _resolve_claude_env_value(module_value: str | None, default_value: str | None, effective_value: str | None) -> str:
    """Resolve patched module config first, then active model profile config."""

    if module_value != default_value:
        return str(module_value or "").strip()
    if module_value is None:
        return ""
    value = str(module_value or "").strip()
    if value:
        return value
    return str(effective_value or "").strip()


def build_claude_env() -> dict[str, str]:
    """Build the Claude SDK environment and report missing config clearly."""

    missing: list[str] = []
    env: dict[str, str] = {}

    api_key = _resolve_claude_env_value(ANTHROPIC_API_KEY, _DEFAULT_ANTHROPIC_API_KEY, get_effective_api_key("claude"))
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    else:
        missing.append("ANTHROPIC_API_KEY")

    base_url = _resolve_claude_env_value(ANTHROPIC_BASE_URL, _DEFAULT_ANTHROPIC_BASE_URL, get_effective_base_url("claude"))
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url

    model = _resolve_claude_env_value(ANTHROPIC_MODEL, _DEFAULT_ANTHROPIC_MODEL, get_effective_model("claude"))
    if model:
        env["ANTHROPIC_MODEL"] = model

    if missing:
        missing_text = "、".join(missing)
        raise ClaudeConfigurationError(
            "Claude Provider 配置不完整，缺少环境变量："
            f"{missing_text}。\n"
            "请在项目根目录 `.env` 中补齐配置后重启服务；如果暂时不用 Claude，"
            "可以设置 `AGENT_PROVIDER=openai`，或运行 `hiclaw model list` 查看可用配置，"
            "并用 `/model use <profile_id>` 切换。"
        )

    return env


def load_prompt_fragment(name: str) -> str | None:
    """从 workspace/prompts/ 加载 prompt 片段，文件不存在时返回 None。"""
    path = PROMPTS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def build_system_prompt(prompt: str, session_scope: str | None = None, decision_plan: DecisionPlan | None = None) -> str:
    """构造当前 Agent 调用使用的 system prompt。"""

    context_snapshot = build_context_snapshot(session_scope, prompt)
    selected_skills, skill_prompt = build_skill_prompt(prompt, decision_plan.selected_skills if decision_plan is not None else None)
    selected_skill_names = ", ".join(skill.name for skill in selected_skills) or "无"
    decision_text = render_decision_plan(decision_plan)

    system_template = load_prompt_fragment("system")
    rules = load_prompt_fragment("rules")
    tools_guide = load_prompt_fragment("tools")

    if system_template:
        system = system_template.format(
            WORKSPACE_DIR=WORKSPACE_DIR,
            CONTEXT_SNAPSHOT=context_snapshot,
            SELECTED_SKILLS=selected_skill_names,
            SKILL_PROMPT=skill_prompt,
        )
    else:
        system = f"""
你现在运行在一个多入口个人智能体系统中。
当前工作区目录是：{WORKSPACE_DIR}

下面是当前可用的分层上下文快照：
{context_snapshot}

本轮命中的 skill：{selected_skill_names}

{skill_prompt}

{decision_text}
""".strip()

    rules_text = rules if rules else """
规则：
1. **联网搜索（最高优先）**：需要搜索互联网信息（天气、新闻、百科等）时，必须调用 web_search 工具，禁止用 curl、wget、bash 等任何方式直接爬取网页替代搜索。违反此规则会导致多余的审批流程。
2. 当用户询问文件、目录或当前时间时，优先使用工具。
3. 如果需要额外主动给当前会话发送一条消息，请使用 send_message 工具。
4. 不要编造文件内容；如果需要文件数据，就调用工具读取。
5. **Bash 工具使用场景**：当任务涉及以下情况时，请优先使用 Bash 工具编写脚本执行：
   - 多步骤文件操作（批量重命名、移动、复制等）
   - 复杂的数据处理（日志分析、格式转换、统计计算等）
   - 需要自动化重复操作时
   - 处理大量文件或目录时
   - 需要生成报告或汇总信息时
6. **Shell 平台适配**：`bash` 工具已按操作系统自动选择 shell。在 Linux/macOS 上直接编写 Bash 命令；在 Windows 上编写 PowerShell 命令。Windows 下的常用 PowerShell 等效命令参考：
   - 移动/重命名：Move-Item、Rename-Item
   - 删除：Remove-Item
   - 复制：Copy-Item
   - 列出文件：Get-ChildItem
   - 读取文件：Get-Content
   - 搜索：Select-String
7. 写 Bash 脚本时，复杂任务建议先写脚本文件再执行。
8. 当前环境里不要默认使用 `python3`，优先尝试 `python`。
9. 当前环境不保证安装了 `gh` 等额外命令行工具，不要默认依赖它们。
10. **任务管理**：你可以使用 list_tasks 工具查看当前会话的待执行任务，使用 cancel_task 工具取消指定任务，使用 create_task 工具创建单次定时任务。
11. 当用户希望你设置提醒、定时通知、稍后执行某事，而规则层没有直接识别成功时，你可以先用 get_current_time 获取当前时间，自己推算目标执行时间，再调用 create_task 创建任务。
12. 当用户提到取消提醒、取消任务时，请先用 list_tasks 确认任务 ID，再调用 cancel_task 取消。
13. 面向用户回复任务列表时，优先用"第1个、第2个"这类自然序号表达，不要默认暴露内部任务 ID，除非用户明确要求查看 ID。
14. 如果你自己查看了任务列表并准备回复给用户，统一使用这种纯文本格式，不要自由发挥：第一行写"你当前的定时任务："；后面每行一条，格式为"1. 时间 | 类型 | 内容"。
15. 当用户只是想看当前任务或提醒时，回复尽量简洁直接，不要用表格，不要加"Boss"等额外称呼，不要主动问"需要调整或取消吗"这类销售式追问。
16. 回答尽量使用自然、清晰的中文。
""".strip()

    tools_text = tools_guide if tools_guide else ""

    parts = [system, rules_text]
    if tools_text:
        parts.append(tools_text)

    return "\n\n".join(parts)


def build_tool_hooks(sender: MessageSender, target_id: str | int, conversation: ConversationRef) -> dict[str, list[HookMatcher]]:
    """构造工具执行过程的当前会话状态通知。"""

    async def notify_tool_start(hook_input, tool_use_id, context) -> dict:
        tool_name = str(hook_input.get("tool_name") or "tool")
        tool_args = hook_input.get("tool_input")
        mark_agent_tool_started(conversation, tool_name, str(tool_args or "")[:160])
        if SHOW_TOOL_TRACE:
            await send_sender_text(sender, target_id, f"[Tool Start] {tool_name}")
        return {}

    async def notify_tool_finish(hook_input, tool_use_id, context) -> dict:
        tool_name = str(hook_input.get("tool_name") or "tool")
        mark_agent_tool_finished(conversation, tool_name, "done")
        if SHOW_TOOL_TRACE:
            await send_sender_text(sender, target_id, f"[Tool Done] {tool_name}")
        return {}

    async def notify_tool_failure(hook_input, tool_use_id, context) -> dict:
        tool_name = str(hook_input.get("tool_name") or "tool")
        error_text = str(hook_input.get("error") or "failed")
        mark_agent_tool_finished(conversation, tool_name, error_text)
        if SHOW_TOOL_TRACE:
            await send_sender_text(sender, target_id, f"[Tool Failed] {tool_name}: {error_text}")
        return {}

    return {
        "PreToolUse": [HookMatcher(hooks=[notify_tool_start])],
        "PostToolUse": [HookMatcher(hooks=[notify_tool_finish])],
        "PostToolUseFailure": [HookMatcher(hooks=[notify_tool_failure])],
    }


async def collect_agent_response(prompt: str, options: ClaudeAgentOptions) -> tuple[str, str | None]:
    final_result = None
    text_parts: list[str] = []
    latest_session_id: str | None = None

    async for message in query(prompt=prompt, options=options):
        if getattr(message, "session_id", None):
            latest_session_id = message.session_id
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage) and message.result:
            final_result = message.result

    return (final_result or "\n".join(text_parts)).strip(), latest_session_id


async def run_agent(
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
) -> str:
    """运行一次 Claude Agent，并负责 session 与对话记录落盘。"""

    claude_env = build_claude_env()
    tool_server = build_mcp_server(
        sender=sender,
        target_id=target_id,
        uploaded_image=uploaded_image,
        channel=channel,
        session_scope=session_scope,
    )
    tool_context = ToolContext(
        sender=sender,
        target_id=target_id,
        uploaded_image=uploaded_image,
        channel=channel,
        session_scope=session_scope,
        enforce_confirmations=hasattr(sender, "confirm_tool_use"),
    )
    allowed_tools = build_claude_allowed_tools(CLAUDE_BASE_ALLOWED_TOOLS, ctx=tool_context)
    conversation = ConversationRef(channel=channel or "unknown", target_id=str(target_id), session_scope=session_scope or f"unknown:{target_id}")
    saved_session_id = load_session_id(session_scope) if continue_session else None
    options = ClaudeAgentOptions(
        permission_mode="acceptEdits",
        env=claude_env,
        cwd=str(WORKSPACE_DIR),
        tools=[],
        system_prompt=build_system_prompt(prompt, session_scope, decision_plan),
        mcp_servers={"hiclaw": tool_server},
        allowed_tools=allowed_tools,
        hooks=build_tool_hooks(sender, target_id, conversation),
        continue_conversation=continue_session and bool(saved_session_id),
        resume=saved_session_id,
    )

    try:
        async with acquire_runtime_lock(session_scope, "claude"):
            response, latest_session_id = await collect_agent_response(prompt, options)
            if not response and saved_session_id:
                logger.warning("Claude returned empty response while resuming session %s; retrying without resume.", saved_session_id)
                retry_options = ClaudeAgentOptions(
                    permission_mode="acceptEdits",
                    env=claude_env,
                    cwd=str(WORKSPACE_DIR),
                    tools=[],
                    system_prompt=build_system_prompt(prompt, session_scope, decision_plan),
                    mcp_servers={"hiclaw": tool_server},
                    allowed_tools=allowed_tools,
                    hooks=build_tool_hooks(sender, target_id, conversation),
                    continue_conversation=False,
                    resume=None,
                )
                response, latest_session_id = await collect_agent_response(prompt, retry_options)
    except ClaudeConfigurationError:
        raise
    except Exception as exc:
        logger.exception("Claude request failed")
        raise ClaudeServiceError("Failed to get response from Claude service.") from exc

    if not response.strip():
        raise ClaudeServiceError("Claude service returned an empty response.")

    if latest_session_id:
        save_session_id(latest_session_id, session_scope)

    append_conversation_record(record_text or prompt, response, latest_session_id if continue_session else None, session_scope)
    return response
