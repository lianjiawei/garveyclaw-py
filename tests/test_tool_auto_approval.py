from __future__ import annotations

from weclaw.capabilities.tools import get_tool_spec


def test_current_conversation_task_and_message_tools_do_not_require_confirmation() -> None:
    create_task = get_tool_spec("create_task")
    send_message = get_tool_spec("send_message")

    assert create_task is not None
    assert send_message is not None
    assert create_task.requires_confirmation() is False
    assert send_message.requires_confirmation() is False
