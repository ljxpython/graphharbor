"""PostgreSQL persistence gates for the production runtime contract."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import select, update


def _schema_uri(database_uri: str, schema: str) -> str:
    """Return a libpq URI whose connections use an isolated search path."""
    parts = urlsplit(database_uri)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["options"] = f"-c search_path={schema},public"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query, quote_via=quote), parts.fragment)
    )


@pytest.mark.asyncio
async def test_empty_schema_migration_is_repeatable(pg_runtime) -> None:
    from langgraph_runtime_pg.database import get_database_uri, to_psycopg_uri
    from langgraph_runtime_pg.migrate import upgrade_head

    schema = f"gh_migration_{uuid4().hex[:12]}"
    base_uri = to_psycopg_uri(get_database_uri())
    with psycopg.connect(base_uri, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    try:
        isolated_uri = _schema_uri(base_uri, schema)
        assert upgrade_head(isolated_uri, version_table_schema=schema) == "006_terminal_events"
        assert upgrade_head(isolated_uri, version_table_schema=schema) == "006_terminal_events"
        with psycopg.connect(isolated_uri) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = %s", (schema,)
                )
            }
        assert {"assistants", "threads", "runs", "run_leases", "runtime_events"} <= tables
    finally:
        with psycopg.connect(base_uri, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def _seed_repository_runs(count: int = 1):
    from langgraph_runtime_pg.database import get_session_factory
    from langgraph_runtime_pg.models import AssistantRow, ThreadRow
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    thread_id = uuid4()
    repository = RunRepository(lease_seconds=5)
    session_factory = get_session_factory()
    async with session_factory() as session:
        session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="persistence-test",
                name="persistence-test",
                config={},
                context={},
                metadata_={},
            )
        )
        session.add(ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}))
        runs = []
        for _ in range(count):
            runs.append(
                await repository.create(
                    session,
                    assistant_id=assistant_id,
                    thread_id=thread_id,
                    kwargs={"input": {}},
                    metadata={},
                    tenant_id=None,
                    project_id=None,
                )
            )
        await session.commit()
    return repository, runs


@pytest.mark.asyncio
async def test_concurrent_claim_has_one_owner(pg_runtime) -> None:
    from langgraph_runtime_pg.database import get_session_factory

    repository, runs = await _seed_repository_runs(count=2)
    session_factory = get_session_factory()

    async def claim(owner: str):
        async with session_factory() as session, session.begin():
            return await repository.claim_next(session, owner)

    claimed = await asyncio.gather(claim("worker-a"), claim("worker-b"))
    assert sum(item is not None for item in claimed) == 1

    async with session_factory() as session:
        statuses = (
            (
                await session.execute(
                    select(runs[0].__class__.status).where(
                        runs[0].__class__.run_id.in_([item.run_id for item in runs])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert statuses.count("running") == 1
    assert statuses.count("pending") == 1


@pytest.mark.asyncio
async def test_claim_survives_pool_restart_and_reaper_requeues(pg_runtime) -> None:
    from langgraph_runtime_pg.database import get_session_factory, start_pool, stop_pool
    from langgraph_runtime_pg.models import RunLeaseRow, RunRow

    repository, runs = await _seed_repository_runs()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        claimed = await repository.claim_next(session, "worker-before-restart")
        assert claimed is not None

    await stop_pool()
    await start_pool()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            update(RunRow)
            .where(RunRow.run_id == runs[0].run_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        recovered = await repository.requeue_expired(session, now=datetime.now(UTC))
        assert recovered == 1
        row = await session.get(RunRow, runs[0].run_id)
        assert row is not None
        assert row.status == "pending"
        assert row.reason == "shutdown_requeue"
        assert (
            await session.scalar(select(RunLeaseRow).where(RunLeaseRow.run_id == runs[0].run_id))
            is None
        )


@pytest.mark.asyncio
async def test_rollback_contract_removes_pending_run_and_retry_state(pg_runtime) -> None:
    from langgraph_runtime_pg import ops
    from langgraph_runtime_pg.database import connect

    assistant_id = uuid4()
    async with connect() as conn:
        await anext(
            await ops.Assistants.put(
                conn,
                assistant_id,
                graph_id="rollback-test",
                name="rollback",
                config={},
                metadata={},
            )
        )
        run = await anext(
            await ops.Runs.put(
                conn,
                assistant_id,
                {"config": {}},
                metadata={},
                prevent_insert_if_inflight=False,
            )
        )
        run_id = run["run_id"]
        thread_id = run["thread_id"]
        await ops.Runs.cancel(
            conn,
            [run_id],
            thread_id=thread_id,
            action="rollback",
        )
        assert await anext(await ops.Runs.get(conn, run_id, thread_id=thread_id), None) is None
        retry_count = await conn.retry_counter.get(run_id)
        assert retry_count == 0
