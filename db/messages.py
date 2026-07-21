from uuid import UUID, uuid4

async def create_message(pool, message_id, chat_id, user_content, assistant_content):
    assistant_message_id = uuid4()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO messages(id, chat_id, role, content)
                VALUES (%s, %s, 'user', %s)
                """,
                (message_id, chat_id, user_content)
            )

            await cur.execute(
                """
                INSERT INTO messages(id, chat_id, role, content, parent_message_id)
                VALUES (%s, %s, 'assistant', %s, %s)
                """,
                (assistant_message_id, chat_id, assistant_content, message_id)
            )

            


async def get_chat_messages(pool, chat_id):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:

            await cur.execute(
                """
                SELECT
                id,
                role,
                content,
                parent_message_id
                FROM messages
                WHERE chat_id = %s
                ORDER BY created_at ASC;
                """,
                (chat_id,),
            )

            rows = await cur.fetchall()

        return [
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "parent_message_id": row["parent_message_id"],
            }
            for row in rows
        ]