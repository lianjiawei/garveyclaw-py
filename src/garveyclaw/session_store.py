import json
import re
from pathlib import Path

from garveyclaw.config import SESSION_FILE


def get_session_file(scope: str | None = None) -> Path:
    """按通道隔离 Agent 连续会话；默认沿用旧的 Telegram 会话文件。"""

    if not scope:
        return SESSION_FILE

    safe_scope = re.sub(r"[^a-zA-Z0-9_-]+", "_", scope.strip()).strip("_")
    if not safe_scope:
        return SESSION_FILE
    return SESSION_FILE.with_name(f"{SESSION_FILE.stem}_{safe_scope}{SESSION_FILE.suffix}")


def load_session_id(scope: str | None = None) -> str | None:
    """读取上一次对话的 session_id。"""

    session_file = get_session_file(scope)
    if not session_file.exists():
        return None

    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    session_id = data.get("session_id")
    return session_id if isinstance(session_id, str) and session_id.strip() else None


def save_session_id(session_id: str, scope: str | None = None) -> None:
    """保存最新 session_id，供下一轮消息恢复连续会话。"""

    get_session_file(scope).write_text(
        json.dumps({"session_id": session_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_session_id(scope: str | None = None) -> None:
    """清空指定通道的本地会话文件。"""

    session_file = get_session_file(scope)
    if session_file.exists():
        session_file.unlink()
