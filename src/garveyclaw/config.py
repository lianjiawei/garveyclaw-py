import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 固定从项目根目录加载 .env，避免从其他目录启动时读不到配置。
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE = DATA_DIR / "garveyclaw_session.json"
TASK_DB_FILE = DATA_DIR / "garveyclaw_tasks.db"

SKILLS_DIR = PROJECT_ROOT / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

WORKSPACE_DIR = Path(os.getenv("WORKSPACE_DIR", str(PROJECT_ROOT / "workspace"))).resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

UPLOADS_DIR = WORKSPACE_DIR / "uploads"
UPLOAD_VOICES_DIR = UPLOADS_DIR / "voices"
UPLOAD_VOICES_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_DIR = WORKSPACE_DIR / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
CLAUDE_MEMORY_FILE = MEMORY_DIR / "CLAUDE.md"
CONVERSATIONS_DIR = MEMORY_DIR / "conversations"
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

SCHEDULER_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "30"))
SHOW_TOOL_TRACE = os.getenv("SHOW_TOOL_TRACE", "0") == "1"

TELEGRAM_CONNECT_TIMEOUT = float(os.getenv("TELEGRAM_CONNECT_TIMEOUT", "30"))
TELEGRAM_READ_TIMEOUT = float(os.getenv("TELEGRAM_READ_TIMEOUT", "30"))
TELEGRAM_WRITE_TIMEOUT = float(os.getenv("TELEGRAM_WRITE_TIMEOUT", "30"))
TELEGRAM_POOL_TIMEOUT = float(os.getenv("TELEGRAM_POOL_TIMEOUT", "30"))
TELEGRAM_POLLING_TIMEOUT = int(os.getenv("TELEGRAM_POLLING_TIMEOUT", "30"))
TELEGRAM_BOOTSTRAP_RETRIES = int(os.getenv("TELEGRAM_BOOTSTRAP_RETRIES", "5"))

# none 表示关闭语音识别，vosk 表示启用本地 Vosk 模型。
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "none")
VOSK_MODEL_DIR = os.getenv("VOSK_MODEL_DIR")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL")

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
    "get_uploaded_image",
    "mcp__garveyclaw__get_current_time",
    "mcp__garveyclaw__list_workspace_files",
    "mcp__garveyclaw__read_workspace_file",
    "mcp__garveyclaw__send_message",
    "mcp__garveyclaw__get_uploaded_image",
]
