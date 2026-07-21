from uuid import UUID


async def get_user_chats(pool, user_id: UUID):

    async with pool.connection() as conn:
        async with conn.cursor() as cur:

            await cur.execute(
                """
                SELECT id, title
                FROM chats
                WHERE user_id = %s
                ORDER BY updated_at DESC;
                """,
                (user_id,),
            )

            return await cur.fetchall()


async def get_chat(pool, chat_id: UUID):

    async with pool.connection() as conn:
        async with conn.cursor() as cur:

            await cur.execute(
                """
                SELECT title
                FROM chats
                WHERE id = %s
                ORDER BY updated_at DESC;
                """,
                (chat_id,),
            )

            return await cur.fetchone()


async def create_chat(pool, chat_id, user_id, title):

    async with pool.connection() as conn:
        async with conn.cursor() as cur:

            await cur.execute(
                """
                INSERT INTO chats(id,user_id,title)
                VALUES(%s,%s,%s);
                """,
                (chat_id, user_id, title),
            )


async def delete_chat(pool, chat_id):

    async with pool.connection() as conn:
        async with conn.cursor() as cur:

            await cur.execute(
                """
                DELETE FROM chats
                WHERE id=%s;
                """,
                (chat_id,),
            )



async def update_chat_title(pool, chat_id, new_chat_title):

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            
            await cur.execute(
                """
                UPDATE chats
                SET title = %s
                WHERE id = %s
                RETURNING title;
                """,
                (new_chat_title, chat_id),
            )

            return await cur.fetchone()