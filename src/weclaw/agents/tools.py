from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from weclaw.capabilities.tools import ToolContext, execute_tool, list_tool_specs
from weclaw.core.delivery import MessageSender


def _build_mcp_tool(spec, ctx: ToolContext):
    @tool(spec.name, spec.description, spec.build_mcp_parameters())
    async def _wrapped(args: dict[str, Any]) -> dict[str, Any]:
        result = await execute_tool(spec.name, args, ctx)
        return result.to_mcp_payload()

    return _wrapped


def build_mcp_server(
    sender: MessageSender,
    target_id: str | int,
    uploaded_image: Any | None = None,
    channel: str | None = None,
    session_scope: str | None = None,
):
    """构造当前会话可用的 MCP 工具集合。"""

    ctx = ToolContext(
        sender=sender,
        target_id=target_id,
        uploaded_image=uploaded_image,
        channel=channel,
        session_scope=session_scope,
        enforce_confirmations=hasattr(sender, "confirm_tool_use"),
    )
    tools = [_build_mcp_tool(spec, ctx) for spec in list_tool_specs(provider="claude", context=ctx)]
    return create_sdk_mcp_server(
        name="weclaw-tools",
        version="2.0.0",
        tools=tools,
    )
