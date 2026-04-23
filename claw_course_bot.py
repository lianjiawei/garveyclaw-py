from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    PermissionResultAllow,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# =========================
# 基础配置
# =========================

# 读取 .env 中的配置，供 Telegram 和 Claude Agent SDK 使用。
load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
OWNER_ID = int(os.environ["OWNER_ID"])


# =========================
# 路径与目录
# =========================

# 这份课程版文件的目标，是把从 ep3 开始逐步学到的能力收敛到一个适合讲解的单文件里：
# - owner 限制
# - 连续会话 session
# - 长期记忆与对话记录
# - Claude 内置工具 + 自定义工具
# - 定时任务（单次 / 每天 / 每周）
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
WORKSPACE_DIR = BASE_DIR / "workspace_course"
MEMORY_DIR = WORKSPACE_DIR / "memory_course"
CONVERSATIONS_DIR = MEMORY_DIR / "conversations"

SESSION_FILE = DATA_DIR / "course_session.json"
TASK_DB_FILE = DATA_DIR / "course_tasks.db"
CLAUDE_MEMORY_FILE = MEMORY_DIR / "CLAUDE.md"
DEMO_FILE = WORKSPACE_DIR / "demo.txt"

DATA_DIR.mkdir(exist_ok=True)
WORKSPACE_DIR.mkdir(exist_ok=True)
MEMORY_DIR.mkdir(exist_ok=True)
CONVERSATIONS_DIR.mkdir(exist_ok=True)


# =========================
# 运行期常量
# =========================

SCHEDULER_INTERVAL_SECONDS = 10

# 用一把全局锁把所有 Agent 调用串起来，方便学习时观察顺序，也避免普通消息和定时任务并发运行。
AGENT_LOCK = asyncio.Lock()
SCHEDULER: AsyncIOScheduler | None = None

WEEKDAY_NAME_TO_INDEX = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}

WEEKDAY_INDEX_TO_LABEL = {
    "0": "每周一",
    "1": "每周二",
    "2": "每周三",
    "3": "每周四",
    "4": "每周五",
    "5": "每周六",
    "6": "每周日",
}

TASK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    schedule_type TEXT NOT NULL DEFAULT 'once',
    schedule_value TEXT,
    next_run TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_run TEXT,
    last_result TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_status ON scheduled_tasks(status);
"""


# =========================
# 数据结构
# =========================

@dataclass(slots=True)
class ParsedSchedule:
    """把自然语言解析成统一的任务结构，便于后续存库和展示。"""

    run_at: datetime
    prompt: str
    schedule_type: str
    schedule_value: str | None


# =========================
# 初始化辅助
# =========================

def ensure_demo_file() -> None:
    """创建一个工作区示例文件，方便测试文件工具。"""
    if DEMO_FILE.exists():
        return

    DEMO_FILE.write_text(
        "这是课程版机器人的示例文件。\n"
        "你可以让 Claude 读取这个文件，并观察 session、memory、tools、scheduler 如何协同工作。\n",
        encoding="utf-8",
    )


def ensure_memory_files() -> None:
    """首次运行时创建长期记忆文件。"""
    if CLAUDE_MEMORY_FILE.exists():
        return

    CLAUDE_MEMORY_FILE.write_text(
        "# 长期记忆\n\n"
        "## 目录说明\n"
        f"- 项目根目录：`{BASE_DIR}`\n"
        f"- 工作区目录：`{WORKSPACE_DIR}`\n"
        f"- 长期记忆文件：`{CLAUDE_MEMORY_FILE}`\n"
        f"- 对话记录目录：`{CONVERSATIONS_DIR}`\n"
        f"- Session 文件：`{SESSION_FILE}`\n"
        f"- 定时任务数据库：`{TASK_DB_FILE}`\n\n"
        "## 文件使用规则\n"
        "- 长期稳定信息写入 CLAUDE.md。\n"
        "- conversations 目录只保存每轮原始对话记录。\n"
        "- 工作区普通文件操作应尽量限制在 workspace_course 内。\n"
        "- 如果用户明确要求“记住”，应优先更新 CLAUDE.md。\n\n"
        "## 用户偏好\n"
        "- 用户喜欢中文讲解与中文关键注释。\n"
        "- 代码学习要主线清晰，尽量少放无关辅助逻辑。\n"
        "- ep 文件用于学习和培训，调通后再迁回正式工程。\n\n"
        "## 课程版目标\n"
        "- 理解 owner 控制、连续会话、长期记忆、自定义工具、定时任务之间如何协同。\n",
        encoding="utf-8",
    )


ensure_demo_file()
ensure_memory_files()


# =========================
# 通用辅助
# =========================

def get_local_now() -> datetime:
    """返回带本地时区信息的当前时间。"""
    return datetime.now().astimezone()


def is_owner(update: Update) -> bool:
    """判断当前 Telegram 消息发送者是不是 owner。"""
    user = update.effective_user
    return bool(user and user.id == OWNER_ID)


def resolve_workspace_path(relative_path: str) -> Path:
    """把相对路径限制在 workspace_course 内，防止越界访问。"""
    candidate = (WORKSPACE_DIR / relative_path).resolve()
    workspace_root = WORKSPACE_DIR.resolve()

    if candidate != workspace_root and workspace_root not in candidate.parents:
        raise ValueError("Path is outside the allowed workspace.")

    return candidate


def normalize_hour(period: str | None, hour: int) -> int:
    """根据中文时间段，把小时归一化到 24 小时制。"""
    if period in {"下午", "晚上"} and 1 <= hour <= 11:
        return hour + 12
    if period == "中午" and 1 <= hour <= 10:
        return hour + 12
    if period in {"早上", "上午"} and hour == 12:
        return 0
    return hour


def compute_next_weekday_run(now: datetime, weekday: int, hour: int, minute: int) -> datetime:
    """计算下一次“每周 X 某时某分”的执行时间。"""
    days_ahead = weekday - now.weekday()
    if days_ahead < 0:
        days_ahead += 7

    target_date = (now + timedelta(days=days_ahead)).date()
    run_at = datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=hour,
        minute=minute,
        tzinfo=now.tzinfo,
    )

    if run_at <= now:
        run_at = run_at + timedelta(days=7)

    return run_at


def format_schedule_description(schedule_type: str, schedule_value: str | None) -> str:
    """把任务调度配置转换成更适合展示给人的中文文本。"""
    if schedule_type == "once":
        return "单次任务"
    if schedule_type == "daily":
        return f"每天 {schedule_value}"
    if schedule_type == "weekly":
        if not schedule_value:
            return "每周任务"
        weekday, time_part = schedule_value.split("|", maxsplit=1)
        return f"{WEEKDAY_INDEX_TO_LABEL.get(weekday, '每周')} {time_part}"
    return schedule_type


# =========================
# 自然语言定时解析
# =========================

def parse_relative_schedule(text: str, now: datetime) -> ParsedSchedule | None:
    """解析“30秒后”“10分钟后”“2小时后”这类相对时间。"""
    patterns = [
        r"^(?P<num>\d+)\s*秒后(?P<task>.+)$",
        r"^(?P<num>\d+)\s*分钟后(?P<task>.+)$",
        r"^(?P<num>\d+)\s*小时后(?P<task>.+)$",
    ]

    for pattern in patterns:
        match = re.match(pattern, text)
        if not match:
            continue

        amount = int(match.group("num"))
        task = match.group("task").strip(" ，。,:：")
        if not task:
            return None

        if "秒后" in pattern:
            run_at = now + timedelta(seconds=amount)
        elif "分钟后" in pattern:
            run_at = now + timedelta(minutes=amount)
        else:
            run_at = now + timedelta(hours=amount)

        return ParsedSchedule(run_at=run_at, prompt=task, schedule_type="once", schedule_value=None)

    return None


def parse_daily_schedule(text: str, now: datetime) -> ParsedSchedule | None:
    """解析“每天下午3点提醒我...”这类每日循环任务。"""
    match = re.match(
        r"^每天(?P<period>早上|上午|中午|下午|晚上)?(?P<hour>\d{1,2})点(?:(?P<minute>\d{1,2})分?)?(?P<task>.+)$",
        text,
    )
    if not match:
        return None

    period = match.group("period")
    hour = normalize_hour(period, int(match.group("hour")))
    minute = int(match.group("minute") or "0")
    task = match.group("task").strip(" ，。,:：")
    if not task or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    run_at = datetime(
        year=now.year,
        month=now.month,
        day=now.day,
        hour=hour,
        minute=minute,
        tzinfo=now.tzinfo,
    )
    if run_at <= now:
        run_at = run_at + timedelta(days=1)

    return ParsedSchedule(
        run_at=run_at,
        prompt=task,
        schedule_type="daily",
        schedule_value=f"{hour:02d}:{minute:02d}",
    )


def parse_weekly_schedule(text: str, now: datetime) -> ParsedSchedule | None:
    """解析“每周一早上9点提醒我...”这类每周循环任务。"""
    match = re.match(
        r"^每周(?P<weekday>[一二三四五六日天])(?P<period>早上|上午|中午|下午|晚上)?"
        r"(?P<hour>\d{1,2})点(?:(?P<minute>\d{1,2})分?)?(?P<task>.+)$",
        text,
    )
    if not match:
        return None

    weekday_text = match.group("weekday")
    period = match.group("period")
    hour = normalize_hour(period, int(match.group("hour")))
    minute = int(match.group("minute") or "0")
    task = match.group("task").strip(" ，。,:：")
    if not task or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    weekday = WEEKDAY_NAME_TO_INDEX[weekday_text]
    run_at = compute_next_weekday_run(now, weekday, hour, minute)

    return ParsedSchedule(
        run_at=run_at,
        prompt=task,
        schedule_type="weekly",
        schedule_value=f"{weekday}|{hour:02d}:{minute:02d}",
    )


def parse_absolute_schedule(text: str, now: datetime) -> ParsedSchedule | None:
    """解析“今晚8点”“明天早上9点”这类绝对时间的单次任务。"""
    match = re.match(
        r"^(?P<day>今天|今晚|明天)(?P<period>早上|上午|中午|下午|晚上)?"
        r"(?P<hour>\d{1,2})点(?:(?P<minute>\d{1,2})分?)?(?P<task>.+)$",
        text,
    )
    if not match:
        return None

    day_word = match.group("day")
    period = match.group("period")
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")
    task = match.group("task").strip(" ，。,:：")
    if not task:
        return None

    if day_word == "今晚" and period is None:
        period = "晚上"

    hour = normalize_hour(period, hour)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    day_offset = 1 if day_word == "明天" else 0
    target_date = (now + timedelta(days=day_offset)).date()
    run_at = datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=hour,
        minute=minute,
        tzinfo=now.tzinfo,
    )

    # 学习版里如果用户说“今天/今晚”而该时间点已过，就顺延到明天，避免刚建立即过期。
    if day_word in {"今天", "今晚"} and run_at <= now:
        run_at = run_at + timedelta(days=1)

    return ParsedSchedule(run_at=run_at, prompt=task, schedule_type="once", schedule_value=None)


def parse_natural_schedule(text: str) -> ParsedSchedule | None:
    """统一解析自然语言定时表达。"""
    normalized = text.strip()
    now = get_local_now()

    parsers = [
        parse_relative_schedule,
        parse_daily_schedule,
        parse_weekly_schedule,
        parse_absolute_schedule,
    ]

    for parser in parsers:
        result = parser(normalized, now)
        if result is not None:
            return result

    return None


# =========================
# Session 与长期记忆
# =========================

def load_session_id() -> str | None:
    """从本地文件中读取连续会话的 session_id。"""
    if not SESSION_FILE.exists():
        return None

    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    session_id = data.get("session_id")
    return session_id if isinstance(session_id, str) and session_id.strip() else None


def save_session_id(session_id: str) -> None:
    """保存最新 session_id，供下一轮恢复连续会话。"""
    SESSION_FILE.write_text(
        json.dumps({"session_id": session_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_session_id() -> None:
    """清空本地 session 文件，让下一轮从新会话开始。"""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


def load_long_term_memory() -> str:
    """读取长期记忆文件内容。"""
    ensure_memory_files()
    return CLAUDE_MEMORY_FILE.read_text(encoding="utf-8")


def append_long_term_memory(note: str) -> None:
    """把一条用户明确要求记住的信息追加到长期记忆中。"""
    ensure_memory_files()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with CLAUDE_MEMORY_FILE.open("a", encoding="utf-8") as file:
        file.write(f"\n## 追加记忆 {timestamp}\n- {note.strip()}\n")


def append_conversation_record(user_message: str, assistant_reply: str, session_id: str | None) -> None:
    """把每轮对话落盘到按天划分的 jsonl 文件中。"""
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


# =========================
# 定时任务数据库
# =========================

async def init_task_db() -> None:
    """初始化数据库，并兼容早期版本的任务表结构。"""
    async with aiosqlite.connect(TASK_DB_FILE) as db:
        await db.executescript(TASK_TABLE_SQL)

        cursor = await db.execute("PRAGMA table_info(scheduled_tasks)")
        table_info = await cursor.fetchall()
        columns = {row[1] for row in table_info}

        if "schedule_type" not in columns:
            await db.execute(
                "ALTER TABLE scheduled_tasks ADD COLUMN schedule_type TEXT NOT NULL DEFAULT 'once'"
            )
        if "schedule_value" not in columns:
            await db.execute("ALTER TABLE scheduled_tasks ADD COLUMN schedule_value TEXT")

        # 兼容旧数据库中 next_run 被定义成 NOT NULL 的情况。
        # 课程版现在允许单次任务执行完成后把 next_run 置空，因此需要把旧表迁移为可空字段。
        next_run_info = next((row for row in table_info if row[1] == "next_run"), None)
        if next_run_info is not None and next_run_info[3] == 1:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks_new (
                    id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    prompt TEXT NOT NULL,
                    schedule_type TEXT NOT NULL DEFAULT 'once',
                    schedule_value TEXT,
                    next_run TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    last_run TEXT,
                    last_result TEXT
                );
                INSERT INTO scheduled_tasks_new (
                    id, chat_id, prompt, schedule_type, schedule_value, next_run, status, created_at, last_run, last_result
                )
                SELECT
                    id, chat_id, prompt, schedule_type, schedule_value, next_run, status, created_at, last_run, last_result
                FROM scheduled_tasks;
                DROP TABLE scheduled_tasks;
                ALTER TABLE scheduled_tasks_new RENAME TO scheduled_tasks;
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_status ON scheduled_tasks(status);
                """
            )

        await db.commit()


async def create_scheduled_task(
    chat_id: int,
    prompt: str,
    run_at: datetime,
    schedule_type: str = "once",
    schedule_value: str | None = None,
) -> str:
    """创建一条定时任务，并返回任务 ID。"""
    task_id = uuid.uuid4().hex[:8]

    async with aiosqlite.connect(TASK_DB_FILE) as db:
        await db.execute(
            """
            INSERT INTO scheduled_tasks (id, chat_id, prompt, schedule_type, schedule_value, next_run, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                chat_id,
                prompt,
                schedule_type,
                schedule_value,
                run_at.astimezone(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()

    return task_id


async def list_scheduled_tasks() -> list[dict[str, Any]]:
    """列出所有还处于 active 状态的任务。"""
    async with aiosqlite.connect(TASK_DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, chat_id, prompt, schedule_type, schedule_value, next_run, status, created_at
            FROM scheduled_tasks
            WHERE status = 'active'
            ORDER BY next_run ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_due_tasks() -> list[dict[str, Any]]:
    """取出已经到点但还没执行的任务。"""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(TASK_DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT *
            FROM scheduled_tasks
            WHERE status = 'active' AND next_run <= ?
            ORDER BY next_run ASC
            """,
            (now,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def update_task_after_run(
    task_id: str,
    result: str,
    next_run: datetime | None,
    status: str,
) -> None:
    """更新任务执行结果、下次时间和状态。"""
    async with aiosqlite.connect(TASK_DB_FILE) as db:
        last_run = datetime.now(timezone.utc).isoformat()

        # 单次任务执行完成后，不再需要 next_run。
        # 但当前学习版表结构把 next_run 设成了 NOT NULL，因此这里不能直接写入 NULL。
        # 处理方式是：
        # - 对循环任务：继续更新 next_run
        # - 对单次任务：只更新状态和结果，保留旧的 next_run 值
        if next_run is None:
            await db.execute(
                """
                UPDATE scheduled_tasks
                SET status = ?, last_run = ?, last_result = ?
                WHERE id = ?
                """,
                (
                    status,
                    last_run,
                    result,
                    task_id,
                ),
            )
        else:
            await db.execute(
                """
                UPDATE scheduled_tasks
                SET status = ?, last_run = ?, last_result = ?, next_run = ?
                WHERE id = ?
                """,
                (
                    status,
                    last_run,
                    result,
                    next_run.astimezone(timezone.utc).isoformat(),
                    task_id,
                ),
            )
        await db.commit()


async def cancel_scheduled_task(task_id: str) -> bool:
    """取消一条尚未执行的任务。"""
    async with aiosqlite.connect(TASK_DB_FILE) as db:
        cursor = await db.execute(
            """
            UPDATE scheduled_tasks
            SET status = 'cancelled'
            WHERE id = ? AND status = 'active'
            """,
            (task_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


# =========================
# 工具层
# =========================

@tool("get_current_time", "获取当前服务器本地时间。", {})
async def get_current_time(_: dict[str, Any]) -> dict[str, Any]:
    """最简单的工具：不带参数，直接返回当前时间。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {"content": [{"type": "text", "text": f"Current local time is: {now}"}]}


@tool("list_workspace_files", "列出学习工作区中的文件和目录。", {})
async def list_workspace_files(_: dict[str, Any]) -> dict[str, Any]:
    """列出工作区内容，便于演示工具如何访问受限目录。"""
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


@tool("read_workspace_file", "读取学习工作区中的文本文件。", {"path": str})
async def read_workspace_file(args: dict[str, Any]) -> dict[str, Any]:
    """读取工作区中的文本文件内容。"""
    relative_path = args["path"]

    try:
        target = resolve_workspace_path(relative_path)
    except ValueError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "is_error": True}

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


def build_mcp_server(bot, chat_id: int):
    """为当前聊天构造一个带 send_message 的 MCP server。"""

    @tool("send_message", "给当前 Telegram 聊天主动发送一条消息。", {"text": str})
    async def send_message(args: dict[str, Any]) -> dict[str, Any]:
        """演示型工具：让 Claude 能主动向当前聊天多发一条消息。"""
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
        name="course-tools",
        version="1.0.0",
        tools=[get_current_time, list_workspace_files, read_workspace_file, send_message],
    )


# =========================
# Agent 调用层
# =========================

def build_system_prompt() -> str:
    """统一构造 system prompt，把长期记忆和目录约束注入进去。"""
    long_term_memory = load_long_term_memory()

    return f"""
你现在运行在一个 Telegram 机器人中，这是一个用于学习 Agent 能力的实验环境。

当前目录信息如下：
- 项目根目录：{BASE_DIR}
- 工作区目录：{WORKSPACE_DIR}
- 长期记忆文件：{CLAUDE_MEMORY_FILE}
- 对话记录目录：{CONVERSATIONS_DIR}

下面是从 CLAUDE.md 读取到的长期记忆：
{long_term_memory}

规则：
1. 当用户询问工作区文件或当前时间时，优先使用工具。
2. 如果需要额外主动给 Telegram 发送一条消息，请使用 send_message 工具。
3. 不要编造文件内容；如果需要文件数据，就调用工具读取。
4. 如果用户要求你“记住”某些长期信息，应把内容写入 CLAUDE.md，而不是只在回复里口头说明。
5. 每轮对话结束后，原始记录会追加保存到 conversations 目录下按日期命名的 jsonl 文件中。
6. 回答时尽量使用自然、清晰的中文。
""".strip()


async def allow_all_tools(*args):
    """学习版里把动态权限统一放行，便于观察工具完整链路。"""
    return PermissionResultAllow(behavior="allow")


async def make_prompt_stream(text: str):
    """启用 can_use_tool 时，需要把 prompt 包装成 AsyncIterable。"""
    yield {
        "type": "user",
        "message": {
            "role": "user",
            "content": text,
        },
    }


async def run_agent(prompt: str, bot, chat_id: int | None, continue_session: bool) -> str:
    """统一封装 Agent 调用，普通消息和定时任务都走这里。"""
    if chat_id is None:
        raise ValueError("chat_id is required for agent execution.")

    env = {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "ANTHROPIC_BASE_URL": ANTHROPIC_BASE_URL,
    }
    saved_session_id = load_session_id() if continue_session else None
    tool_server = build_mcp_server(bot=bot, chat_id=chat_id)

    async def notify_tool_start(hook_input, tool_use_id, context) -> dict[str, Any]:
        await bot.send_message(chat_id=chat_id, text=f"[Tool Start] {hook_input['tool_name']}")
        return {}

    async def notify_tool_finish(hook_input, tool_use_id, context) -> dict[str, Any]:
        await bot.send_message(chat_id=chat_id, text=f"[Tool Done] {hook_input['tool_name']}")
        return {}

    async def notify_tool_failure(hook_input, tool_use_id, context) -> dict[str, Any]:
        await bot.send_message(
            chat_id=chat_id,
            text=f"[Tool Failed] {hook_input['tool_name']}: {hook_input['error']}",
        )
        return {}

    options = ClaudeAgentOptions(
        permission_mode="acceptEdits",
        env=env,
        # 这里使用项目根目录作为 cwd，便于 Claude 内置工具访问 workspace、memory 和 data。
        cwd=str(BASE_DIR),
        tools={"type": "preset", "preset": "claude_code"},
        system_prompt=build_system_prompt(),
        allowed_tools=[
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
            "mcp__memory__get_current_time",
            "mcp__memory__list_workspace_files",
            "mcp__memory__read_workspace_file",
            "mcp__memory__send_message",
        ],
        mcp_servers={"memory": tool_server},
        hooks={
            "PreToolUse": [HookMatcher(hooks=[notify_tool_start])],
            "PostToolUse": [HookMatcher(hooks=[notify_tool_finish])],
            "PostToolUseFailure": [HookMatcher(hooks=[notify_tool_failure])],
        },
        can_use_tool=allow_all_tools,
        continue_conversation=continue_session and bool(saved_session_id),
        resume=saved_session_id,
    )

    final_result = None
    text_parts: list[str] = []
    latest_session_id: str | None = None

    async for message in query(prompt=make_prompt_stream(prompt), options=options):
        if getattr(message, "session_id", None):
            latest_session_id = message.session_id

        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage) and message.result:
            final_result = message.result

    if latest_session_id:
        save_session_id(latest_session_id)

    response = final_result or "\n".join(text_parts) or "I did not receive a response from Claude."
    append_conversation_record(prompt, response, latest_session_id if continue_session else None)
    return response


async def ask_claude(prompt: str, update: Update) -> str:
    """普通消息入口：直接把用户文本交给 Agent。"""
    return await run_agent(
        prompt=prompt,
        bot=update.get_bot(),
        chat_id=update.effective_chat.id if update.effective_chat else None,
        continue_session=True,
    )


# =========================
# 调度器执行层
# =========================

def compute_next_run_after_execution(task: dict[str, Any]) -> tuple[datetime | None, str]:
    """根据任务类型计算执行后的下一次时间。"""
    schedule_type = task.get("schedule_type", "once")
    schedule_value = task.get("schedule_value")

    if schedule_type == "daily" and schedule_value:
        hour_text, minute_text = schedule_value.split(":", maxsplit=1)
        now = get_local_now()
        next_run = datetime(
            year=now.year,
            month=now.month,
            day=now.day,
            hour=int(hour_text),
            minute=int(minute_text),
            tzinfo=now.tzinfo,
        ) + timedelta(days=1)
        return next_run, "active"

    if schedule_type == "weekly" and schedule_value:
        weekday_text, time_part = schedule_value.split("|", maxsplit=1)
        hour_text, minute_text = time_part.split(":", maxsplit=1)
        next_run = compute_next_weekday_run(
            get_local_now(),
            int(weekday_text),
            int(hour_text),
            int(minute_text),
        )
        return next_run, "active"

    return None, "completed"


async def execute_scheduled_task(task: dict[str, Any], bot) -> None:
    """执行一条到点任务，并决定它是结束还是排到下一次。"""
    task_id = task["id"]
    chat_id = task["chat_id"]
    prompt = task["prompt"]

    wrapped_prompt = (
        "你正在执行一条定时任务。"
        "请根据任务要求完成回答；如果需要额外主动通知 Telegram，可以使用 send_message 工具。\n\n"
        f"任务内容：{prompt}"
    )

    try:
        async with AGENT_LOCK:
            result = await run_agent(
                prompt=wrapped_prompt,
                bot=bot,
                chat_id=chat_id,
                continue_session=False,
            )
            await bot.send_message(chat_id=chat_id, text=f"⏰ 定时任务执行结果：\n{result}")

        next_run, next_status = compute_next_run_after_execution(task)
        await update_task_after_run(task_id, result, next_run, next_status)
    except Exception as exc:
        error_text = f"定时任务执行失败：{exc}"
        await bot.send_message(chat_id=chat_id, text=error_text)
        await update_task_after_run(task_id, error_text, None, "completed")


async def check_due_tasks(bot) -> None:
    """周期性检查数据库中的到点任务。"""
    due_tasks = await get_due_tasks()
    for task in due_tasks:
        await execute_scheduled_task(task, bot)


def setup_scheduler(bot) -> AsyncIOScheduler:
    """创建异步调度器，固定频率扫描到点任务。"""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_due_tasks,
        "interval",
        seconds=SCHEDULER_INTERVAL_SECONDS,
        args=[bot],
        id="course_check_tasks",
        replace_existing=True,
    )
    return scheduler


# =========================
# Telegram 命令与消息入口
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """展示课程版机器人支持的学习能力和示例命令。"""
    if not update.message or not is_owner(update):
        return

    await update.message.reply_text(
        "你好，这是 claw 课程版机器人。\n\n"
        "这个文件把从 ep3 开始逐步学到的能力收拢到了一起：\n"
        "- owner 限制\n"
        "- 连续会话 session\n"
        "- 长期记忆与对话记录\n"
        "- Claude 内置工具 + 自定义工具\n"
        "- 单次 / 每天 / 每周定时任务\n\n"
        "你可以直接试这些输入：\n"
        "- 现在几点了？\n"
        "- 列出工作区里的文件。\n"
        "- 读取 demo.txt 的内容。\n"
        "- 30秒后提醒我喝水。\n"
        "- 今晚8点提醒我开会。\n"
        "- 明天早上9点提醒我整理日报。\n"
        "- 每天下午3点提醒我喝水。\n"
        "- 每周一早上9点提醒我开例会。\n\n"
        "可用命令：\n"
        "- /schedule_in 秒数 任务内容\n"
        "- /tasks\n"
        "- /cancel 任务ID\n"
        "- /memory\n"
        "- /remember 内容\n"
        "- /reset"
    )


async def reset_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """清空本地 session，让下一次消息从新会话开始。"""
    if not update.message or not is_owner(update):
        return

    clear_session_id()
    await update.message.reply_text("当前会话已清空，下一条消息会开启新会话。")


async def schedule_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """命令式创建一条若干秒后执行的单次定时任务。"""
    if not update.message or not is_owner(update):
        return

    if len(context.args) < 2:
        await update.message.reply_text("用法：/schedule_in 秒数 任务内容")
        return

    try:
        delay_seconds = int(context.args[0])
    except ValueError:
        await update.message.reply_text("秒数必须是整数，例如：/schedule_in 60 1分钟后提醒我喝水")
        return

    if delay_seconds <= 0:
        await update.message.reply_text("秒数必须大于 0。")
        return

    prompt = " ".join(context.args[1:]).strip()
    if not prompt:
        await update.message.reply_text("任务内容不能为空。")
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        await update.message.reply_text("当前消息没有可用的 chat_id。")
        return

    run_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
    task_id = await create_scheduled_task(chat_id, prompt, run_at)
    local_time = run_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    await update.message.reply_text(
        f"定时任务已创建。\n"
        f"- 任务ID：{task_id}\n"
        f"- 类型：单次任务\n"
        f"- 执行时间：{local_time}\n"
        f"- 内容：{prompt}"
    )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """列出当前还在等待执行的任务。"""
    if not update.message or not is_owner(update):
        return

    tasks = await list_scheduled_tasks()
    if not tasks:
        await update.message.reply_text("当前没有待执行的定时任务。")
        return

    lines = ["当前待执行任务："]
    for task in tasks:
        local_time = datetime.fromisoformat(task["next_run"]).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        schedule_desc = format_schedule_description(task.get("schedule_type", "once"), task.get("schedule_value"))
        lines.append(f"- {task['id']} | {schedule_desc} | {local_time} | {task['prompt']}")

    await update.message.reply_text("\n".join(lines))


async def cancel_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """取消一条还未执行的任务。"""
    if not update.message or not is_owner(update):
        return

    if not context.args:
        await update.message.reply_text("用法：/cancel 任务ID")
        return

    task_id = context.args[0].strip()
    success = await cancel_scheduled_task(task_id)
    if success:
        await update.message.reply_text(f"任务 {task_id} 已取消。")
    else:
        await update.message.reply_text(f"没有找到可取消的任务：{task_id}")


async def show_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看当前长期记忆文件内容。"""
    if not update.message or not is_owner(update):
        return

    await update.message.reply_text(load_long_term_memory())


async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """把一条长期信息追加到 CLAUDE.md。"""
    if not update.message or not is_owner(update):
        return

    memory_note = " ".join(context.args).strip()
    if not memory_note:
        await update.message.reply_text("用法：/remember 这里填写要写入长期记忆的内容")
        return

    append_long_term_memory(memory_note)
    await update.message.reply_text("长期记忆已更新。")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """普通文本入口：优先识别自然语言定时，否则进入普通 Claude 对话。"""
    if not update.message or not update.message.text or not is_owner(update):
        return

    natural_schedule = parse_natural_schedule(update.message.text)
    if natural_schedule is not None:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is None:
            await update.message.reply_text("当前消息没有可用的 chat_id。")
            return

        task_id = await create_scheduled_task(
            chat_id=chat_id,
            prompt=natural_schedule.prompt,
            run_at=natural_schedule.run_at,
            schedule_type=natural_schedule.schedule_type,
            schedule_value=natural_schedule.schedule_value,
        )
        local_time = natural_schedule.run_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        await update.message.reply_text(
            f"我已按自然语言理解为一条定时任务。\n"
            f"- 任务ID：{task_id}\n"
            f"- 类型：{format_schedule_description(natural_schedule.schedule_type, natural_schedule.schedule_value)}\n"
            f"- 执行时间：{local_time}\n"
            f"- 内容：{natural_schedule.prompt}"
        )
        return

    async with AGENT_LOCK:
        response = await ask_claude(update.message.text, update)
        await update.message.reply_text(response, disable_web_page_preview=True)


# =========================
# 启动入口
# =========================

async def post_init(application: Application) -> None:
    """启动 bot 后，初始化数据库并启动调度器。"""
    global SCHEDULER

    await init_task_db()
    SCHEDULER = setup_scheduler(application.bot)
    SCHEDULER.start()


def build_application() -> Application:
    """构造 Telegram Application，并注册所有命令与消息入口。"""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("schedule_in", schedule_in))
    app.add_handler(CommandHandler("tasks", list_tasks))
    app.add_handler(CommandHandler("cancel", cancel_task))
    app.add_handler(CommandHandler("reset", reset_session))
    app.add_handler(CommandHandler("memory", show_memory))
    app.add_handler(CommandHandler("remember", remember))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


def main() -> None:
    """启动课程版 Telegram 机器人。"""
    app = build_application()

    print("claw 课程版机器人已启动...")
    print(f"工作区目录: {WORKSPACE_DIR}")
    print(f"Session 文件: {SESSION_FILE}")
    print(f"任务数据库: {TASK_DB_FILE}")
    print(f"长期记忆文件: {CLAUDE_MEMORY_FILE}")
    print(f"对话记录目录: {CONVERSATIONS_DIR}")
    # run_polling() 会启动 Telegram 的长轮询主循环。
    # 启动后，程序会持续向 Telegram 拉取新消息，并把消息分发给上面注册的 handler。
    app.run_polling()


if __name__ == "__main__":
    main()
