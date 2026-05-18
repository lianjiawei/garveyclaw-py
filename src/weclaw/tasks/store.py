import aiosqlite

from weclaw.config import TASK_DB_FILE

TASK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    channel TEXT NOT NULL DEFAULT 'telegram',
    target_id TEXT,
    prompt TEXT NOT NULL,
    schedule_type TEXT NOT NULL DEFAULT 'once',
    schedule_value TEXT,
    session_scope TEXT,
    continue_session INTEGER NOT NULL DEFAULT 0,
    next_run TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_run TEXT,
    last_result TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_status ON scheduled_tasks(status);
"""


async def init_task_db() -> None:
    # 初始化定时任务数据库，并兼容旧表结构。
    async with aiosqlite.connect(TASK_DB_FILE) as db:
        await db.executescript(TASK_TABLE_SQL)

        cursor = await db.execute("PRAGMA table_info(scheduled_tasks)")
        table_info = await cursor.fetchall()
        columns = {row[1] for row in table_info}

        if "channel" not in columns:
            await db.execute("ALTER TABLE scheduled_tasks ADD COLUMN channel TEXT NOT NULL DEFAULT 'telegram'")
        if "target_id" not in columns:
            await db.execute("ALTER TABLE scheduled_tasks ADD COLUMN target_id TEXT")
        if "schedule_type" not in columns:
            await db.execute(
                "ALTER TABLE scheduled_tasks ADD COLUMN schedule_type TEXT NOT NULL DEFAULT 'once'"
            )
        if "schedule_value" not in columns:
            await db.execute("ALTER TABLE scheduled_tasks ADD COLUMN schedule_value TEXT")
        if "session_scope" not in columns:
            await db.execute("ALTER TABLE scheduled_tasks ADD COLUMN session_scope TEXT")
        if "continue_session" not in columns:
            await db.execute("ALTER TABLE scheduled_tasks ADD COLUMN continue_session INTEGER NOT NULL DEFAULT 0")

        next_run_info = next((row for row in table_info if row[1] == "next_run"), None)
        if next_run_info is not None and next_run_info[3] == 1:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks_new (
                    id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'telegram',
                    target_id TEXT,
                    prompt TEXT NOT NULL,
                    schedule_type TEXT NOT NULL DEFAULT 'once',
                    schedule_value TEXT,
                    session_scope TEXT,
                    continue_session INTEGER NOT NULL DEFAULT 0,
                    next_run TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    last_run TEXT,
                    last_result TEXT
                );
                INSERT INTO scheduled_tasks_new (
                    id, chat_id, channel, target_id, prompt, schedule_type, schedule_value, next_run, status, created_at, last_run, last_result
                )
                SELECT
                    id, chat_id, 'telegram', CAST(chat_id AS TEXT), prompt, schedule_type, schedule_value, next_run, status, created_at, last_run, last_result
                FROM scheduled_tasks;
                DROP TABLE scheduled_tasks;
                ALTER TABLE scheduled_tasks_new RENAME TO scheduled_tasks;
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_status ON scheduled_tasks(status);
                """
            )

        await db.commit()
