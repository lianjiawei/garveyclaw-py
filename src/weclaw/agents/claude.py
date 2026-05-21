from __future__ import annotations



import logging

from typing import TYPE_CHECKING, Any



from claude_agent_sdk import (

    AssistantMessage,

    ClaudeAgentOptions,

    HookMatcher,

    ResultMessage,

    TextBlock,

    query,

)



from weclaw.capabilities.tools import ToolContext, build_claude_allowed_tools

from weclaw.agents.prompts import build_claude_instructions
from weclaw.agents.tools import build_mcp_server

from weclaw.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    ANTHROPIC_MODEL,
    SHOW_TOOL_TRACE,
    WORKSPACE_DIR,
)
from weclaw.core.delivery import MessageSender, send_sender_text
from weclaw.core.agent_activity import mark_agent_tool_finished, mark_agent_tool_started
from weclaw.core.provider_model import get_effective_api_key, get_effective_base_url, get_effective_model
from weclaw.decision.models import DecisionPlan

from weclaw.memory.store import append_conversation_record

from weclaw.core.locks import acquire_runtime_lock

from weclaw.core.types import ConversationRef

from weclaw.memory.session import load_session_id, save_session_id


if TYPE_CHECKING:

    pass



logger = logging.getLogger(__name__)

CLAUDE_BASE_ALLOWED_TOOLS: list[str] = []
_DEFAULT_ANTHROPIC_API_KEY = ANTHROPIC_API_KEY
_DEFAULT_ANTHROPIC_BASE_URL = ANTHROPIC_BASE_URL
_DEFAULT_ANTHROPIC_MODEL = ANTHROPIC_MODEL

IMAGE_UNAVAILABLE_MARKERS = (
    "无法查看图片",
    "无法看到图片",
    "看不到图片",
    "不能查看图片",
    "不能看到图片",
    "没有收到图片",
    "无法访问图片",
    "无法直接查看",
    "can't see the image",
    "cannot see the image",
    "can't view the image",
    "cannot view the image",
    "unable to view the image",
    "no image",
)




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
            "可以设置 `AGENT_PROVIDER=openai`，或运行 `weclaw model list` 查看可用配置，"
            "并用 `/model use <profile_id>` 切换。"
        )


    return env


def response_missed_uploaded_image(response: str, uploaded_image: Any | None) -> bool:
    if uploaded_image is None:
        return False
    lowered = response.lower()
    return any(marker.lower() in lowered for marker in IMAGE_UNAVAILABLE_MARKERS)





def load_prompt_fragment(name: str) -> str | None:

    """从 workspace/prompts/ 加载 prompt 片段，文件不存在时返回 None。"""

    from weclaw.agents.prompts import load_prompt_fragment as load_shared_prompt_fragment

    return load_shared_prompt_fragment(name)





def build_system_prompt(
    prompt: str,
    session_scope: str | None = None,
    decision_plan: DecisionPlan | None = None,
    *,
    has_uploaded_image: bool = False,
) -> str:

    """构造当前 Agent 调用使用的 system prompt。"""

    return build_claude_instructions(prompt, session_scope, decision_plan, has_uploaded_image=has_uploaded_image)





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

        system_prompt=build_system_prompt(prompt, session_scope, decision_plan, has_uploaded_image=uploaded_image is not None),

        mcp_servers={"weclaw": tool_server},

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

                    system_prompt=build_system_prompt(prompt, session_scope, decision_plan, has_uploaded_image=uploaded_image is not None),

                    mcp_servers={"weclaw": tool_server},

                    allowed_tools=allowed_tools,

                    hooks=build_tool_hooks(sender, target_id, conversation),

                    continue_conversation=False,

                    resume=None,

                )

                response, latest_session_id = await collect_agent_response(prompt, retry_options)

            if response_missed_uploaded_image(response, uploaded_image):

                logger.warning("Claude response appears to miss uploaded image; retrying with explicit image tool instruction.")

                image_retry_options = ClaudeAgentOptions(

                    permission_mode="acceptEdits",

                    env=claude_env,

                    cwd=str(WORKSPACE_DIR),

                    tools=[],

                    system_prompt=build_system_prompt(prompt, session_scope, decision_plan, has_uploaded_image=True),

                    mcp_servers={"weclaw": tool_server},

                    allowed_tools=allowed_tools,

                    hooks=build_tool_hooks(sender, target_id, conversation),

                    continue_conversation=False,

                    resume=None,

                )

                image_retry_prompt = (
                    "本轮用户上传了图片。你上一轮回复似乎没有成功查看图片。"
                    "请先调用 get_uploaded_image 工具获取图片内容，再结合图片和用户说明直接回答。\n\n"
                    f"用户原始请求：{prompt}"
                )

                retry_response, retry_session_id = await collect_agent_response(image_retry_prompt, image_retry_options)

                if retry_response.strip() and not response_missed_uploaded_image(retry_response, uploaded_image):
                    response = retry_response
                    latest_session_id = retry_session_id or latest_session_id

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
