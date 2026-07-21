from uuid import UUID

async def add_document(
    pool,
    document_id: UUID,
    user_id: UUID,
    filename: str
):
    async with pool.connection() as conn:    
        async with conn.cursor() as cur:

            await cur.execute(
                """
                INSERT INTO documents (
                    id,
                    user_id,
                    filename
                )
                VALUES (%s, %s, %s);
                """,
                (
                    document_id,
                    user_id,
                    filename
                ),
            )



async def get_user_documents(
    pool,
    user_id: UUID,
):
    async with pool.connection() as conn:    
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    id,
                    filename,
                    uploaded_at
                FROM documents
                WHERE user_id = %s
                ORDER BY uploaded_at DESC;
                """,
                (user_id,),
            )

            rows = await cur.fetchall()

        return [
            {
                "id": row["id"],
                "filename": row["filename"],
                "uploaded_at": row["uploaded_at"],
            }
            for row in rows
        ]


async def get_document(
    pool,
    document_id: UUID,
):
    async with pool.connection() as conn:    
        async with conn.cursor() as cur:

            await cur.execute(
                """
                SELECT
                    id,
                    user_id,
                    filename,
                    uploaded_at
                FROM documents
                WHERE id = %s;
                """,
                (document_id,),
            )

            row = await cur.fetchone()

        if row is None:
            return None

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "filename": row["filename"],
            "uploaded_at": row["uploaded_at"],
        }


async def delete_document(
    pool,
    document_id: UUID,
):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM documents
                WHERE id = %s;
                """,
                (document_id,),
            )         