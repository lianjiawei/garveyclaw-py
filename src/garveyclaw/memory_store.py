import json
from datetime import datetime

from garveyclaw.config import CLAUDE_MEMORY_FILE, CONVERSATIONS_DIR, MEMORY_DIR, PROJECT_ROOT, WORKSPACE_DIR


def ensure_memory_files() -> None:
    # 首次运行时自动创建长期记忆文件和对话记录目录。
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    if CLAUDE_MEMORY_FILE.exists():
        return

    CLAUDE_MEMORY_FILE.write_text(
        "# 长期记忆\n\n"
        "## 目录说明\n"
        f"- 项目根目录：`{PROJECT_ROOT}`\n"
        f"- 工作区目录：`{WORKSPACE_DIR}`\n"
        f"- 长期记忆文件：`{CLAUDE_MEMORY_FILE}`\n"
        f"- 对话记录目录：`{CONVERSATIONS_DIR}`\n\n"
        "## 文件使用规则\n"
        "- 长期稳定信息写入 CLAUDE.md。\n"
        "- 每轮对话原始记录追加写入 conversations 目录。\n"
        "- 工作区文件操作尽量限制在工作区目录内。\n\n"
        "## 默认背景\n"
        "- 当前项目是一个基于 Telegram Bot 和 Claude Agent SDK 的个人 Agent。\n"
        "- 新功能通常会先在 ep 学习文件里验证，再迁回正式工程。\n",
        encoding="utf-8",
    )


def load_long_term_memory() -> str:
    # 读取长期记忆文件，并在缺失时自动补齐默认内容。
    ensure_memory_files()
    return CLAUDE_MEMORY_FILE.read_text(encoding="utf-8")


def append_long_term_memory(note: str) -> None:
    # 把一条新的长期记忆追加到 CLAUDE.md。
    ensure_memory_files()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with CLAUDE_MEMORY_FILE.open("a", encoding="utf-8") as file:
        file.write(f"\n## 追加记忆 {timestamp}\n- {note.strip()}\n")


def append_conversation_record(user_message: str, assistant_reply: str, session_id: str | None) -> None:
    # 每天维护一个 jsonl 文件，逐行追加对话记录，便于后续查看和处理。
    ensure_memory_files()
    date_key = datetime.now().strftime("%Y-%m-%d")
    file_path = CONVERSATIONS_DIR / f"{date_key}.jsonl"
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "user_message": user_message,
        "assistant_reply": assistant_reply,
    }
    with file_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")
