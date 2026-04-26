from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from garveyclaw.config import WORKSPACE_DIR


def resolve_workspace_path(relative_path: str) -> Path:
    """把相对路径限制在工作区内，避免工具访问工作区之外的文件。"""

    candidate = (WORKSPACE_DIR / relative_path).resolve()
    workspace_root = WORKSPACE_DIR.resolve()

    if candidate != workspace_root and workspace_root not in candidate.parents:
        raise ValueError("Path is outside the allowed workspace.")

    return candidate


@tool("get_current_time", "获取当前服务器本地时间。", {})
async def get_current_time(_: dict[str, Any]) -> dict[str, Any]:
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


def build_mcp_server(bot: Any, chat_id: int, uploaded_image: Any | None = None):
    """构造当前 Telegram 会话可用的 MCP 工具集合。"""

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

    @tool("get_uploaded_image", "获取本轮 Telegram 上传的图片内容。", {})
    async def get_uploaded_image(_: dict[str, Any]) -> dict[str, Any]:
        if uploaded_image is None:
            return {
                "content": [{"type": "text", "text": "No image was uploaded in this turn."}],
                "is_error": True,
            }

        return {
            "content": [
                {
                    "type": "text",
                    "text": "This is the image uploaded by the Telegram user in the current turn.",
                },
                {
                    "type": "image",
                    "data": base64.b64encode(uploaded_image.data).decode("ascii"),
                    "mimeType": uploaded_image.mime_type,
                },
            ]
        }

    tools = [
        get_current_time,
        list_workspace_files,
        read_workspace_file,
        send_message,
    ]
    if uploaded_image is not None:
        tools.append(get_uploaded_image)

    return create_sdk_mcp_server(
        name="garveyclaw-tools",
        version="1.0.0",
        tools=tools,
    )
