import json

from garveyclaw.config import SESSION_FILE


def load_session_id() -> str | None:
    # 从本地文件读取上一次对话的 session_id。
    if not SESSION_FILE.exists():
        return None

    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    session_id = data.get("session_id")
    return session_id if isinstance(session_id, str) and session_id.strip() else None


def save_session_id(session_id: str) -> None:
    # 把最新 session_id 落盘，供下一次消息恢复连续会话。
    SESSION_FILE.write_text(
        json.dumps({"session_id": session_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_session_id() -> None:
    # 清空本地会话文件，让下一次消息从新会话开始。
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
