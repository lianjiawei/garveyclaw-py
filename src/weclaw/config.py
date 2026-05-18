import os

from pathlib import Path



from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PLACEHOLDER_MARKERS = (
    "your_",
    "your-",
    "_here",
    "example",
    "changeme",
    "replace_me",
)


# 固定从项目根目录加载 .env，避免从其他目录启动时读不到配置。

load_dotenv(PROJECT_ROOT / ".env")


def is_effectively_configured(value: str | None) -> bool:
    if value is None:
        return False
    stripped = value.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    return not any(marker in lowered for marker in PLACEHOLDER_MARKERS)

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SESSION_FILE = DATA_DIR / "weclaw_session.json"

TASK_DB_FILE = DATA_DIR / "weclaw_tasks.db"

SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "86400"))



SKILLS_DIR = PROJECT_ROOT / "skills"

SKILLS_DIR.mkdir(parents=True, exist_ok=True)



CAPABILITIES_DIR = Path(os.getenv("CAPABILITIES_DIR", str(PROJECT_ROOT / "capabilities"))).resolve()

CAPABILITIES_DIR.mkdir(parents=True, exist_ok=True)

WORKFLOW_DEFINITIONS_DIR = Path(os.getenv("WORKFLOW_DEFINITIONS_DIR", str(CAPABILITIES_DIR / "workflows"))).resolve()

WORKFLOW_DEFINITIONS_DIR.mkdir(parents=True, exist_ok=True)

TOOL_DEFINITIONS_DIR = Path(os.getenv("TOOL_DEFINITIONS_DIR", str(CAPABILITIES_DIR / "tools"))).resolve()
TOOL_DEFINITIONS_DIR.mkdir(parents=True, exist_ok=True)
AGENT_DEFINITIONS_DIR = Path(os.getenv("AGENT_DEFINITIONS_DIR", str(CAPABILITIES_DIR / "agents"))).resolve()
AGENT_DEFINITIONS_DIR.mkdir(parents=True, exist_ok=True)

WORKSPACE_DIR = Path(os.getenv("WORKSPACE_DIR", str(PROJECT_ROOT / "workspace"))).resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


UPLOADS_DIR = WORKSPACE_DIR / "uploads"

UPLOAD_VOICES_DIR = UPLOADS_DIR / "voices"

UPLOAD_VOICES_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_FILES_DIR = UPLOADS_DIR / "files"

UPLOAD_FILES_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_MAX_FILE_SIZE_BYTES = int(os.getenv("UPLOAD_MAX_FILE_SIZE_MB", "10")) * 1024 * 1024

TUI_OUTPUT_DIR = Path(os.getenv("TUI_OUTPUT_DIR", str(WORKSPACE_DIR / "outputs" / "tui"))).resolve()

TUI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)



MEMORY_DIR = WORKSPACE_DIR / "memory"

MEMORY_DIR.mkdir(parents=True, exist_ok=True)

CLAUDE_MEMORY_FILE = MEMORY_DIR / "CLAUDE.md"

CONVERSATIONS_DIR = MEMORY_DIR / "conversations"

CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

LONG_TERM_MEMORY_DIR = MEMORY_DIR / "long_term"

LONG_TERM_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_CANDIDATES_DIR = MEMORY_DIR / "candidates"

MEMORY_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_ARCHIVE_DIR = MEMORY_DIR / "archive"

MEMORY_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_REPORTS_DIR = MEMORY_DIR / "reports"

MEMORY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_CONFLICTS_FILE = MEMORY_DIR / "conflicts.jsonl"

WORKING_STATE_FILE = MEMORY_DIR / "working_state.json"

MONITOR_ACTIVITY_FILE = DATA_DIR / "monitor_activity.json"
CLUSTER_RUNTIME_FILE = DATA_DIR / "cluster_runtime.json"
SESSION_SUMMARIES_DIR = MEMORY_DIR / "session_summaries"
SESSION_SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)


MEMORY_CANDIDATE_AUTO_PROMOTE_SECONDS = int(os.getenv("MEMORY_CANDIDATE_AUTO_PROMOTE_SECONDS", "86400"))

MEMORY_ARCHIVE_AFTER_DAYS = int(os.getenv("MEMORY_ARCHIVE_AFTER_DAYS", "30"))

CONVERSATION_RETENTION_DAYS = int(os.getenv("CONVERSATION_RETENTION_DAYS", "30"))



SCHEDULER_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "5"))

SHOW_TOOL_TRACE = os.getenv("SHOW_TOOL_TRACE", "0") == "1"

AGENT_PROVIDER = os.getenv("AGENT_ROUTE") or os.getenv("AGENT_PROVIDER", "openai")
MODEL_PROFILE_NAME = os.getenv("MODEL_PROFILE_NAME", "")


TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

TAVILY_SEARCH_DEPTH = os.getenv("TAVILY_SEARCH_DEPTH", "basic")

TAVILY_MAX_RESULTS = int(os.getenv("TAVILY_MAX_RESULTS", "5"))

OPENAI_ALLOWED_TOOLS = os.getenv("OPENAI_ALLOWED_TOOLS", "")

CAPABILITY_WATCHER_ENABLED = os.getenv("CAPABILITY_WATCHER_ENABLED", "1") == "1"

CAPABILITY_WATCHER_INTERVAL_SECONDS = float(os.getenv("CAPABILITY_WATCHER_INTERVAL_SECONDS", "1.0"))



TELEGRAM_CONNECT_TIMEOUT = float(os.getenv("TELEGRAM_CONNECT_TIMEOUT", "30"))

TELEGRAM_READ_TIMEOUT = float(os.getenv("TELEGRAM_READ_TIMEOUT", "30"))

TELEGRAM_WRITE_TIMEOUT = float(os.getenv("TELEGRAM_WRITE_TIMEOUT", "30"))

TELEGRAM_POOL_TIMEOUT = float(os.getenv("TELEGRAM_POOL_TIMEOUT", "30"))

TELEGRAM_POLLING_TIMEOUT = int(os.getenv("TELEGRAM_POLLING_TIMEOUT", "30"))

TELEGRAM_BOOTSTRAP_RETRIES = int(os.getenv("TELEGRAM_BOOTSTRAP_RETRIES", "5"))

TELEGRAM_RESTART_DELAY_SECONDS = int(os.getenv("TELEGRAM_RESTART_DELAY_SECONDS", "10"))
TELEGRAM_API_RETRIES = int(os.getenv("TELEGRAM_API_RETRIES", "2"))
TELEGRAM_API_RETRY_DELAY_SECONDS = float(os.getenv("TELEGRAM_API_RETRY_DELAY_SECONDS", "1.5"))


# none 表示关闭语音识别，vosk 表示启用本地 Vosk 模型。

ASR_PROVIDER = os.getenv("ASR_PROVIDER", "none")

VOSK_MODEL_DIR = os.getenv("VOSK_MODEL_DIR")



TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID_RAW = os.getenv("OWNER_ID")
try:
    OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW else None
except ValueError:
    OWNER_ID = None


FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")

FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")

FEISHU_ALLOWED_OPEN_IDS = os.getenv("FEISHU_ALLOWED_OPEN_IDS", "")
FEISHU_ALLOWED_CHAT_IDS = os.getenv("FEISHU_ALLOWED_CHAT_IDS", "")
FEISHU_SESSION_SCOPE_PREFIX = os.getenv("FEISHU_SESSION_SCOPE_PREFIX", "feishu")
FEISHU_REPLY_PROCESSING_MESSAGE = os.getenv("FEISHU_REPLY_PROCESSING_MESSAGE", "1") == "1"
FEISHU_RESTART_DELAY_SECONDS = int(os.getenv("FEISHU_RESTART_DELAY_SECONDS", "10"))
FEISHU_API_RETRIES = int(os.getenv("FEISHU_API_RETRIES", "2"))
FEISHU_API_RETRY_DELAY_SECONDS = float(os.getenv("FEISHU_API_RETRY_DELAY_SECONDS", "1.5"))

WEIXIN_ACCOUNT_ID = os.getenv("WEIXIN_ACCOUNT_ID", "").strip()
WEIXIN_TOKEN = os.getenv("WEIXIN_TOKEN", "").strip()
WEIXIN_BASE_URL = os.getenv("WEIXIN_BASE_URL", "https://ilinkai.weixin.qq.com").strip()
WEIXIN_CDN_BASE_URL = os.getenv("WEIXIN_CDN_BASE_URL", "https://novac2c.cdn.weixin.qq.com/c2c").strip()
WEIXIN_DM_POLICY = os.getenv("WEIXIN_DM_POLICY", "open").strip().lower()
WEIXIN_GROUP_POLICY = os.getenv("WEIXIN_GROUP_POLICY", "disabled").strip().lower()
WEIXIN_ALLOWED_USERS = os.getenv("WEIXIN_ALLOWED_USERS", "")
WEIXIN_GROUP_ALLOWED_USERS = os.getenv("WEIXIN_GROUP_ALLOWED_USERS", "")
WEIXIN_LONG_POLL_TIMEOUT_MS = int(os.getenv("WEIXIN_LONG_POLL_TIMEOUT_MS", "35000"))
WEIXIN_RESTART_DELAY_SECONDS = int(os.getenv("WEIXIN_RESTART_DELAY_SECONDS", "10"))
WEIXIN_SEND_CHUNK_DELAY_SECONDS = float(os.getenv("WEIXIN_SEND_CHUNK_DELAY_SECONDS", "1.5"))
WEIXIN_SEND_CHUNK_RETRIES = int(os.getenv("WEIXIN_SEND_CHUNK_RETRIES", "4"))
WEIXIN_SEND_CHUNK_RETRY_DELAY_SECONDS = float(os.getenv("WEIXIN_SEND_CHUNK_RETRY_DELAY_SECONDS", "1.0"))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL")

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL")



OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_IMAGE_API_KEY = os.getenv("OPENAI_IMAGE_API_KEY")

OPENAI_IMAGE_BASE_URL = os.getenv("OPENAI_IMAGE_BASE_URL")

OPENAI_IMAGE_GENERATE_PATH = os.getenv("OPENAI_IMAGE_GENERATE_PATH", "/images/generations")

OPENAI_IMAGE_EDIT_PATH = os.getenv("OPENAI_IMAGE_EDIT_PATH", "/images/edits")

OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")

OPENAI_IMAGE_SIZE = os.getenv("OPENAI_IMAGE_SIZE", "1024x1024")

OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "auto")

OPENAI_IMAGE_OUTPUT_FORMAT = os.getenv("OPENAI_IMAGE_OUTPUT_FORMAT", "png")

OPENAI_IMAGE_TIMEOUT_SECONDS = float(os.getenv("OPENAI_IMAGE_TIMEOUT_SECONDS", "120"))

OPENAI_IMAGE_INCLUDE_OPTIONAL_PARAMS = os.getenv("OPENAI_IMAGE_INCLUDE_OPTIONAL_PARAMS", "1") == "1"



DECISION_INTERPRETER_MODE = os.getenv("DECISION_INTERPRETER_MODE", "auto").strip().lower()
DECISION_INTERPRETER_TIMEOUT_SECONDS = float(os.getenv("DECISION_INTERPRETER_TIMEOUT_SECONDS", "20"))
DECISION_INTERPRETER_MAX_PROMPT_CHARS = int(os.getenv("DECISION_INTERPRETER_MAX_PROMPT_CHARS", "2000"))
AGENT_CLUSTER_ENABLED = os.getenv("AGENT_CLUSTER_ENABLED", "0") == "1"
AGENT_CLUSTER_REVIEW_ENABLED = os.getenv("AGENT_CLUSTER_REVIEW_ENABLED", "1") == "1"
AGENT_CLUSTER_MAX_EVENTS = int(os.getenv("AGENT_CLUSTER_MAX_EVENTS", "40"))
AGENT_CLUSTER_ORCHESTRATOR_ENABLED = os.getenv("AGENT_CLUSTER_ORCHESTRATOR_ENABLED", "0") == "1"
AGENT_CLUSTER_DYNAMIC_PLANNER_ENABLED = os.getenv("AGENT_CLUSTER_DYNAMIC_PLANNER_ENABLED", "0") == "1"
