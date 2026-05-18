from __future__ import annotations

from typing import Any

from weclaw.capabilities.tools import (
    ToolContext,
    build_openai_tool_definitions,
    execute_tool,
    get_tool_spec,
    parse_openai_allowed_tools,
)
from weclaw.core.agent_activity import mark_agent_tool_finished, mark_agent_tool_started

OpenAIToolContext = ToolContext


def build_openai_tools(ctx: OpenAIToolContext | None = None) -> list[dict[str, Any]]:
    allowed_names = parse_openai_allowed_tools()
    return build_openai_tool_definitions(ctx=ctx, allowed_names=allowed_names)


async def execute_openai_tool(name: str, arguments: dict[str, Any], ctx: OpenAIToolContext) -> str:
    conversation = ctx.conversation
    spec = get_tool_spec(name)
    tool_summary = spec.build_summary(arguments) if spec is not None else ""
    if conversation is not None:
        mark_agent_tool_started(conversation, name, tool_summary)

    result_text = ""
    try:
        result = await execute_tool(name, arguments, ctx)
        result_text = result.to_text()
        return result_text
    finally:
        if conversation is not None:
            mark_agent_tool_finished(conversation, name, result_text[:160] if result_text else "")
