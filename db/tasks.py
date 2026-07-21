# db/tasks.py
from uuid import UUID
from datetime import datetime
from psycopg.types.json import Json


async def create_task(
    pool,
    task_id: UUID,
    user_id: UUID,
    chat_id: UUID,
    task_type: str,
    date_time: datetime,
    task_description: str | None = None,
    task_payload: dict | None = None,
):
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO tasks (task_id, user_id, chat_id, task_type, task_description, task_payload, date_time)
            VALUES (%(task_id)s, %(user_id)s, %(chat_id)s, %(task_type)s, %(task_description)s, %(task_payload)s, %(date_time)s)
            """,
            {
                "task_id": task_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "task_type": task_type,
                "task_description": task_description,
                "task_payload": Json(task_payload or {}),
                "date_time": date_time,
            },
        )


async def get_due_tasks(pool, now: datetime):
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'pending' AND date_time <= %(now)s
            ORDER BY date_time ASC
            """,
            {"now": now},
        )
        return await cur.fetchall()


async def update_task_status(pool, task_id: UUID, status: str, error_message: str | None = None):
    async with pool.connection() as conn:
        await conn.execute(
            """
            UPDATE tasks SET status = %(status)s, error_message = %(error_message)s
            WHERE task_id = %(task_id)s
            """,
            {"status": status, "error_message": error_message, "task_id": task_id},
        )


async def cancel_task(pool, task_id: UUID, user_id: UUID):
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            UPDATE tasks SET status = 'cancelled'
            WHERE task_id = %(task_id)s AND user_id = %(user_id)s AND status = 'pending'
            RETURNING task_id
            """,
            {"task_id": task_id, "user_id": user_id},
        )
        return await cur.fetchone()


async def get_user_tasks(pool, user_id: UUID, chat_id: UUID | None = None):
    query = "SELECT * FROM tasks WHERE user_id = %(user_id)s"
    params = {"user_id": user_id}
    if chat_id:
        query += " AND chat_id = %(chat_id)s"
        params["chat_id"] = chat_id
    query += " ORDER BY date_time ASC"

    async with pool.connection() as conn:
        cur = await conn.execute(query, params)
        return await cur.fetchall()