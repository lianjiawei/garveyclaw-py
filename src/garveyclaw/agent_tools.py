from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from garveyclaw.config import WORKSPACE_DIR


def resolve_workspace_path(relative_path: str) -> Path:
    # 把相对路径解析到工作区，并阻止工具访问工作区之外的文件。
    candidate = (WORKSPACE_DIR / relative_path).resolve()
    workspace_root = WORKSPACE_DIR.resolve()

    if candidate != workspace_root and workspace_root not in candidate.parents:
        raise ValueError("Path is outside the allowed workspace.")

    return candidate


@tool("get_current_time", "获取当前服务器本地时间。", {})
async def get_current_time(_: dict[str, Any]) -> dict[str, Any]:
    # 演示最简单的无参工具。
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "content": [
            {
                "type": "text",
                "text": f"Current local time is: {now}",
            }
        ]
    }


@tool("list_workspace_files", "列出工作区中的文件和目录。", {})
async def list_workspace_files(_: dict[str, Any]) -> dict[str, Any]:
    # 展示当前工作区有哪些顶层文件，方便模型先建立目录感知。
    items = sorted(path.name for path in WORKSPACE_DIR.iterdir())
    text = "\n".join(f"- {name}" for name in items) if items else "(workspace is empty)"
    return {
        "content": [
            {
                "type": "text",
                "text": f"Workspace directory: {WORKSPACE_DIR}\n{text}",
            }
        ]
    }


@tool("read_workspace_file", "读取工作区中的文本文件。", {"path": str})
async def read_workspace_file(args: dict[str, Any]) -> dict[str, Any]:
    # 文件读取工具只接受工作区内的相对路径。
    relative_path = args["path"]

    try:
        target = resolve_workspace_path(relative_path)
    except ValueError as exc:
        return {
            "content": [{"type": "text", "text": str(exc)}],
            "is_error": True,
        }

    if not target.exists():
        return {
            "content": [{"type": "text", "text": f"File not found: {relative_path}"}],
            "is_error": True,
        }

    if not target.is_file():
        return {
            "content": [{"type": "text", "text": f"Not a file: {relative_path}"}],
            "is_error": True,
        }

    content = target.read_text(encoding="utf-8", errors="replace")
    return {
        "content": [
            {
                "type": "text",
                "text": f"File: {relative_path}\n\n{content}",
            }
        ]
    }


def build_mcp_server(bot: Any, chat_id: int):
    # 这里把和当前 Telegram 会话相关的工具实例化出来。
    @tool("send_message", "向当前 Telegram 会话额外发送一条消息。", {"text": str})
    async def send_message(args: dict[str, Any]) -> dict[str, Any]:
        text = args["text"]
        await bot.send_message(chat_id=chat_id, text=text)
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Message sent to the Telegram chat successfully.",
                }
            ]
        }

    return create_sdk_mcp_server(
        name="garveyclaw-tools",
        version="1.0.0",
        tools=[
            get_current_time,
            list_workspace_files,
            read_workspace_file,
            send_message,
        ],
    )
