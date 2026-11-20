import asyncio
from uuid import uuid4

from langgraph_runtime_pg.database import connect, to_psycopg_uri
from langgraph_runtime_pg.schema_setup import run_schema_setup


async def test_waiting_setup_does_not_block_concurrent_index_build(pg_runtime):
    from sqlalchemy import text

    table = "schema_setup_" + uuid4().hex
    async with connect() as conn:
        await conn.session.execute(text(f"CREATE TABLE {table} (id integer)"))
    first_locked = asyncio.Event()
    finished = []

    async def first(connection):
        first_locked.set()
        await asyncio.sleep(0.2)
        await connection.execute(f"CREATE INDEX CONCURRENTLY {table}_idx ON {table}(id)")
        finished.append("first")

    async def second(connection):
        assert finished == ["first"]
        finished.append("second")

    first_task = asyncio.create_task(run_schema_setup(to_psycopg_uri(), first))
    await first_locked.wait()
    await asyncio.wait_for(
        asyncio.gather(first_task, run_schema_setup(to_psycopg_uri(), second)), 10
    )
    assert finished == ["first", "second"]
    async with connect() as conn:
        await conn.session.execute(text(f"DROP TABLE {table}"))
