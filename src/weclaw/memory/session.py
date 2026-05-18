from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import aiosqlite

from weclaw.config import SESSION_FILE, SESSION_TIMEOUT_SECONDS, TASK_DB_FILE

SESSION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    scope TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at);
"""


def get_session_file(scope: str | None = None) -> Path:
    """按通道隔离 Agent 连续会话；默认沿用旧的 Telegram 会话文件。"""

    if not scope:
        return SESSION_FILE

    safe_scope = re.sub(r"[^a-zA-Z0-9_-]+", "_", scope.strip()).strip("_")
    if not safe_scope:
        return SESSION_FILE
    if len(safe_scope) > 128:
        safe_scope = safe_scope[:128]
    return SESSION_FILE.with_name(f"{SESSION_FILE.stem}_{safe_scope}{SESSION_FILE.suffix}")


@contextmanager
def _file_lock(file_path: Path):
    """跨平台文件锁，防止并发写入损坏。"""
    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
    fd = None
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fd is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)


async def init_session_db() -> None:
    """初始化 session SQLite 数据库。"""
    async with aiosqlite.connect(TASK_DB_FILE) as db:
        await db.executescript(SESSION_TABLE_SQL)
        await db.commit()


async def load_session_id_async(scope: str | None = None) -> str | None:
    """从 SQLite 读取 session_id（异步版本）。"""
    safe_scope = scope if scope else "default"
    async with aiosqlite.connect(TASK_DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT session_id, updated_at FROM sessions WHERE scope = ?",
            (safe_scope,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        # 检查超时
        try:
            updated_at = time.mktime(time.strptime(row["updated_at"], "%Y-%m-%dT%H:%M:%S"))
            if time.time() - updated_at > SESSION_TIMEOUT_SECONDS:
                await db.execute("DELETE FROM sessions WHERE scope = ?", (safe_scope,))
                await db.commit()
                return None
        except (ValueError, OverflowError):
            pass

        session_id = row["session_id"]
        return session_id if isinstance(session_id, str) and session_id.strip() else None


async def save_session_id_async(session_id: str, scope: str | None = None) -> None:
    """保存 session_id 到 SQLite（异步版本）。"""
    safe_scope = scope if scope else "default"
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    async with aiosqlite.connect(TASK_DB_FILE) as db:
        await db.execute(
            """
            INSERT INTO sessions (scope, session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                session_id = excluded.session_id,
                updated_at = excluded.updated_at
            """,
            (safe_scope, session_id, now, now),
        )
        await db.commit()


async def clear_session_id_async(scope: str | None = None) -> None:
    """从 SQLite 删除 session（异步版本）。"""
    safe_scope = scope if scope else "default"
    async with aiosqlite.connect(TASK_DB_FILE) as db:
        await db.execute("DELETE FROM sessions WHERE scope = ?", (safe_scope,))
        await db.commit()


def load_session_id(scope: str | None = None) -> str | None:
    """读取上一次对话的 session_id，超时自动清除（同步版本，保持向后兼容）。"""

    session_file = get_session_file(scope)
    if not session_file.exists():
        return None

    try:
        mtime = session_file.stat().st_mtime
        if time.time() - mtime > SESSION_TIMEOUT_SECONDS:
            session_file.unlink()
            return None
    except OSError:
        return None

    try:
        with _file_lock(session_file):
            data = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    session_id = data.get("session_id")
    return session_id if isinstance(session_id, str) and session_id.strip() else None


def save_session_id(session_id: str, scope: str | None = None) -> None:
    """保存最新 session_id，供下一轮消息恢复连续会话（同步版本，保持向后兼容）。"""

    session_file = get_session_file(scope)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"session_id": session_id}, ensure_ascii=False, indent=2)
    with _file_lock(session_file):
        tmp_fd, tmp_path = tempfile.mkstemp(dir=session_file.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_f:
                tmp_f.write(content)
            os.replace(tmp_path, session_file)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def clear_session_id(scope: str | None = None) -> None:
    """清空指定通道的本地会话文件。"""

    session_file = get_session_file(scope)
    if session_file.exists():
        session_file.unlink()
