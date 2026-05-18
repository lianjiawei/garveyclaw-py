from __future__ import annotations



from datetime import datetime, timezone

from typing import Any



import aiosqlite



from weclaw.config import TASK_DB_FILE

from weclaw.core.types import ConversationRef





async def create_scheduled_task_record(

    task_id: str,

    conversation: ConversationRef,

    prompt: str,

    run_at: datetime,

    schedule_type: str = "once",

    schedule_value: str | None = None,

    continue_session: bool = False,

) -> None:

    chat_id = int(conversation.target_id) if conversation.target_id.isdigit() else 0

    async with aiosqlite.connect(TASK_DB_FILE) as db:

        await db.execute(

            """

            INSERT INTO scheduled_tasks (id, chat_id, channel, target_id, prompt, schedule_type, schedule_value, session_scope, continue_session, next_run, created_at)

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            """,

            (

                task_id,

                chat_id,

                conversation.channel,

                conversation.target_id,

                prompt,

                schedule_type,

                schedule_value,

                conversation.session_scope,

                1 if continue_session else 0,

                run_at.astimezone(timezone.utc).isoformat(),

                datetime.now(timezone.utc).isoformat(),

            ),

        )

        await db.commit()





async def list_scheduled_task_records(channel: str | None = None, target_id: str | None = None) -> list[dict[str, Any]]:

    async with aiosqlite.connect(TASK_DB_FILE) as db:

        db.row_factory = aiosqlite.Row

        conditions = ["status IN ('active', 'running')"]

        params: list[Any] = []

        if channel is not None:

            conditions.append("channel = ?")

            params.append(channel)

        if target_id is not None:

            conditions.append("target_id = ?")

            params.append(target_id)

        cursor = await db.execute(

            f"""

            SELECT id, chat_id, channel, target_id, prompt, schedule_type, schedule_value, session_scope, continue_session, next_run, status, created_at

            FROM scheduled_tasks

            WHERE {' AND '.join(conditions)}

            ORDER BY next_run ASC

            """,

            params,

        )

        rows = await cursor.fetchall()

        return [dict(row) for row in rows]





async def list_due_task_record_ids() -> list[str]:

    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(TASK_DB_FILE) as db:

        cursor = await db.execute(

            """

            SELECT id

            FROM scheduled_tasks

            WHERE status = 'active' AND next_run <= ?

            ORDER BY next_run ASC

            """,

            (now,),

        )

        rows = await cursor.fetchall()

        return [str(row[0]) for row in rows]





async def claim_scheduled_task_record(task_id: str) -> dict[str, Any] | None:

    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(TASK_DB_FILE) as db:

        db.row_factory = aiosqlite.Row

        await db.execute("BEGIN IMMEDIATE")

        cursor = await db.execute(

            """

            UPDATE scheduled_tasks

            SET status = 'running'

            WHERE id = ? AND status = 'active' AND next_run <= ?

            """,

            (task_id, now),

        )

        if cursor.rowcount <= 0:

            await db.rollback()

            return None



        cursor = await db.execute(

            """

            SELECT *

            FROM scheduled_tasks

            WHERE id = ?

            """,

            (task_id,),

        )

        row = await cursor.fetchone()

        await db.commit()

        if row is None:

            return None

        

        task_data = dict(row)

        if task_data.get("status") != "running":

            return None

            

        return task_data





async def release_claimed_task_record(task_id: str) -> None:

    async with aiosqlite.connect(TASK_DB_FILE) as db:

        await db.execute(

            """

            UPDATE scheduled_tasks

            SET status = 'active'

            WHERE id = ? AND status = 'running'

            """,

            (task_id,),

        )

        await db.commit()





async def hard_cancel_task_record(task_id: str) -> bool:

    """强制取消任务，即使它正在运行。用于 scheduler 检测到任务已被取消时的清理。"""

    async with aiosqlite.connect(TASK_DB_FILE) as db:

        cursor = await db.execute(

            """

            UPDATE scheduled_tasks

            SET status = 'cancelled'

            WHERE id = ? AND status IN ('active', 'running')

            """,

            (task_id,),

        )

        await db.commit()

        return cursor.rowcount > 0





async def update_task_record_after_run(

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

                WHERE id = ? AND status = 'running'

                """,

                (status, last_run, result, task_id),

            )

        else:

            await db.execute(

                """

                UPDATE scheduled_tasks

                SET status = ?, last_run = ?, last_result = ?, next_run = ?

                WHERE id = ? AND status = 'running'

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





async def cancel_scheduled_task_record(task_id: str, channel: str | None = None, target_id: str | None = None) -> bool:

    async with aiosqlite.connect(TASK_DB_FILE) as db:

        conditions = ["id = ?", "status = 'active'"]

        params: list[Any] = [task_id]

        if channel is not None:

            conditions.append("channel = ?")

            params.append(channel)

        if target_id is not None:

            conditions.append("target_id = ?")

            params.append(target_id)

        cursor = await db.execute(

            """

            UPDATE scheduled_tasks

            SET status = 'cancelled'

            WHERE %s

            """ % " AND ".join(conditions),

            params,

        )

        await db.commit()

        return cursor.rowcount > 0
