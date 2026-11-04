import pytest
from sqlalchemy import text

from backend.db.session import engine


@pytest.mark.asyncio
async def test_pgvector_extension_and_cosine_distance_queries():
    """Verify PostgreSQL pgvector extension, native vector(384) column, and cosine distance query."""
    async with engine.connect() as conn:
        # Check pgvector extension availability in PostgreSQL
        res = await conn.execute(
            text("SELECT default_version, installed_version FROM pg_available_extensions WHERE name = 'vector'")
        )
        row = res.fetchone()
        assert row is not None, "pgvector extension must be available in PostgreSQL"

    # 384-dimensional test vectors matching production model dimension
    v1 = [1.0] + [0.0] * 383
    v2 = [0.0, 1.0] + [0.0] * 382
    v3 = [0.9, 0.1] + [0.0] * 382

    v1_str = "[" + ", ".join(str(x) for x in v1) + "]"
    v2_str = "[" + ", ".join(str(x) for x in v2) + "]"
    v3_str = "[" + ", ".join(str(x) for x in v3) + "]"

    # Use a separate transaction that is rolled back so the database remains clean
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))

            await conn.execute(
                text("""
                CREATE TEMP TABLE test_vectors (
                    id serial PRIMARY KEY,
                    embedding vector(384)
                );
            """)
            )

            # Insert vectors: v1 along dimension 0, v2 along dimension 1, v3 close to dimension 0
            await conn.execute(
                text(f"""
                INSERT INTO test_vectors (embedding) VALUES
                    ('{v1_str}'),
                    ('{v2_str}'),
                    ('{v3_str}');
            """)
            )

            # Query cosine distance (<=>) from v1
            query_res = await conn.execute(
                text(f"""
                SELECT id, embedding <=> '{v1_str}' AS distance
                FROM test_vectors
                ORDER BY distance ASC;
            """)
            )
            rows = query_res.fetchall()
            assert len(rows) == 3

            # Closest item must be id 1 (distance 0.0)
            assert rows[0][0] == 1
            assert abs(rows[0][1] - 0.0) < 1e-5

            # Second closest must be id 3 (small distance)
            assert rows[1][0] == 3
            assert rows[1][1] < 0.1

            # Orthogonal vector (id 2) has cosine distance 1.0
            assert rows[2][0] == 2
            assert abs(rows[2][1] - 1.0) < 1e-5

        finally:
            await trans.rollback()

    await engine.dispose()
