import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from garveyclaw.claude_client import run_agent
from garveyclaw.config import SCHEDULER_INTERVAL_SECONDS, TASK_DB_FILE

logger = logging.getLogger(__name__)

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


@dataclass(slots=True)
class ParsedSchedule:
    # 自然语言解析后的统一任务结构。
    run_at: datetime
    prompt: str
    schedule_type: str
    schedule_value: str | None


def get_local_now() -> datetime:
    return datetime.now().astimezone()


def normalize_hour(period: str | None, hour: int) -> int:
    if period in {"下午", "晚上"} and 1 <= hour <= 11:
        return hour + 12
    if period == "中午" and 1 <= hour <= 10:
        return hour + 12
    if period in {"早上", "上午"} and hour == 12:
        return 0
    return hour


def compute_next_weekday_run(now: datetime, weekday: int, hour: int, minute: int) -> datetime:
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


def parse_relative_schedule(text: str, now: datetime) -> ParsedSchedule | None:
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

    if day_word in {"今天", "今晚"} and run_at <= now:
        run_at = run_at + timedelta(days=1)

    return ParsedSchedule(run_at=run_at, prompt=task, schedule_type="once", schedule_value=None)


def parse_natural_schedule(text: str) -> ParsedSchedule | None:
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


async def create_scheduled_task(
    chat_id: int,
    prompt: str,
    run_at: datetime,
    schedule_type: str = "once",
    schedule_value: str | None = None,
) -> str:
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
    async with aiosqlite.connect(TASK_DB_FILE) as db:
        last_run = datetime.now(timezone.utc).isoformat()
        if next_run is None:
            await db.execute(
                """
                UPDATE scheduled_tasks
                SET status = ?, last_run = ?, last_result = ?, next_run = NULL
                WHERE id = ?
                """,
                (status, last_run, result, task_id),
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


def compute_next_run_after_execution(task: dict[str, Any]) -> tuple[datetime | None, str]:
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
    task_id = task["id"]
    chat_id = task["chat_id"]
    prompt = task["prompt"]

    wrapped_prompt = (
        "你正在执行一条定时任务。"
        "请根据任务要求完成回答；如果需要额外主动通知 Telegram，请使用 send_message 工具。\n\n"
        f"任务内容：{prompt}"
    )

    try:
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
        logger.exception("Scheduled task failed: %s", task_id)
        error_text = f"定时任务执行失败：{exc}"
        await bot.send_message(chat_id=chat_id, text=error_text)
        await update_task_after_run(task_id, error_text, None, "completed")


async def check_due_tasks(bot) -> None:
    due_tasks = await get_due_tasks()
    for task in due_tasks:
        await execute_scheduled_task(task, bot)


def setup_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_due_tasks,
        "interval",
        seconds=SCHEDULER_INTERVAL_SECONDS,
        args=[bot],
        id="garveyclaw_check_tasks",
        replace_existing=True,
    )
    return scheduler

