import os
from pathlib import Path

from dotenv import load_dotenv

# 从 .env 加载运行配置，保持本地开发和部署读取方式一致。
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE = DATA_DIR / "garveyclaw_session.json"
TASK_DB_FILE = DATA_DIR / "garveyclaw_tasks.db"

WORKSPACE_DIR = Path(os.getenv("WORKSPACE_DIR", str(PROJECT_ROOT / "workspace"))).resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_DIR = WORKSPACE_DIR / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
CLAUDE_MEMORY_FILE = MEMORY_DIR / "CLAUDE.md"
CONVERSATIONS_DIR = MEMORY_DIR / "conversations"
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
SCHEDULER_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "10"))

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "qwen3.6-plus")

CLAUDE_TOOLS_PRESET = {"type": "preset", "preset": "claude_code"}

ALLOWED_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "Bash",
    "get_current_time",
    "list_workspace_files",
    "read_workspace_file",
    "send_message",
    "mcp__garveyclaw__get_current_time",
    "mcp__garveyclaw__list_workspace_files",
    "mcp__garveyclaw__read_workspace_file",
    "mcp__garveyclaw__send_message",
]
