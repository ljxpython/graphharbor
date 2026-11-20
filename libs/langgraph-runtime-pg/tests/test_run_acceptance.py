"""Real PostgreSQL acceptance atomicity, replay, authorization and retention."""

import asyncio
import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from langgraph_sdk import Auth
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import IntegrityError

from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.models import AssistantRow, RunAcceptanceRow, RunRow, ThreadRow
from langgraph_runtime_pg.run_acceptance import (
    AcceptanceGrant,
    RunAcceptanceRepository,
    request_digest,
)
from langhost import core_api, server


@pytest.fixture
async def receipt_api(pg_runtime, monkeypatch):
    aid, tid = uuid4(), uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(assistant_id=aid, graph_id="test", metadata_={"owner": "alice"})
        )
        conn.session.add(ThreadRow(thread_id=tid, metadata_={"owner": "alice"}))
    auth = Auth()
    enqueued = []

    @auth.authenticate
    async def authenticate(authorization):
        value = json.loads(authorization)
        return {
            "identity": "alice",
            "run_acceptance": value["grant"],
            "filter_owner": value.get("owner", "alice"),
        }

    @auth.on.threads.create_run
    @auth.on.threads.read
    async def authorize(ctx, value):
        assert value["run_acceptance_receipt"] is True
        return {"owner": ctx.user.filter_owner}

    async def enqueue(run_id):
        enqueued.append(str(run_id))

    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_SECRET", "fixture-only-secret")
    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    monkeypatch.setattr(core_api, "enqueue_run", enqueue)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        yield client, aid, tid, enqueued


def headers(tid, body, key="retry", operation="create", owner="alice", **binding):
    grant = {
        "scope_id": "a" * 64,
        "credential_id": "b" * 64,
        "operation": operation,
        "thread_id": str(tid),
        "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "request_digest": request_digest(body),
        **binding,
    }
    return {
        "Authorization": json.dumps({"grant": grant, "owner": owner}),
        "Content-Type": "application/json",
        "Idempotency-Key": key,
        "GraphHarbor-Request-Digest": grant["request_digest"],
    }


async def create(client, tid, body, **kwargs):
    return await client.post(
        f"/threads/{tid}/runs/acceptance", content=body, headers=headers(tid, body, **kwargs)
    )


async def read(client, tid, body, **kwargs):
    return await client.get(
        f"/threads/{tid}/runs/acceptance", headers=headers(tid, body, operation="read", **kwargs)
    )


@pytest.mark.asyncio
async def test_concurrent_replay_conflict_and_deletion(receipt_api):
    client, aid, tid, enqueued = receipt_api
    body = json.dumps({"assistant_id": str(aid), "input": {"x": 1}}).encode()
    assert (await read(client, tid, body)).json()["result"] == "unknown"
    outcomes = await asyncio.gather(*(create(client, tid, body) for _ in range(8)))
    assert all(r.status_code == 200 for r in outcomes), [r.text for r in outcomes]
    ids = {r.json()["run_id"] for r in outcomes}
    assert len(ids) == 1 and enqueued == list(ids)
    run_id = UUID(ids.pop())
    async with connect() as conn:
        run = await conn.session.get(RunRow, run_id)
        original = dict(run.kwargs)
        assert await conn.session.scalar(select(func.count()).select_from(RunAcceptanceRow)) == 1
    assert (await create(client, tid, body + b" ")).status_code == 409
    other = uuid4()
    assert (await create(client, other, body)).status_code == 409
    assert (await read(client, tid, body, owner="bob")).status_code == 404
    assert (await create(client, tid, body, owner="bob")).status_code == 404
    assert (await read(client, tid, body, credential_id="c" * 64)).json()["result"] == "unknown"
    for _ in range(3):
        receipt = await read(client, tid, body)
        assert receipt.headers["cache-control"] == "no-store"
        assert receipt.json()["result"] == "accepted"
        assert not {"input", "metadata", "scope_id", "authorization_metadata"}.intersection(
            receipt.json()
        )
    async with connect() as conn:
        assert (await conn.session.get(RunRow, run_id)).kwargs == original
        await conn.session.execute(delete(RunRow).where(RunRow.run_id == run_id))
        await conn.session.execute(delete(ThreadRow).where(ThreadRow.thread_id == tid))
        await conn.session.execute(delete(AssistantRow).where(AssistantRow.assistant_id == aid))
    receipt = (await read(client, tid, body)).json()
    assert receipt["run_id"] == str(run_id) and receipt["run_state"] == {
        "available": False,
        "status": None,
    }
    assert (await create(client, tid, body)).json()["run_id"] == str(run_id)
    assert len(enqueued) == 1


@pytest.mark.asyncio
async def test_only_persistent_busy_rejection_closes_key(receipt_api):
    client, aid, tid, enqueued = receipt_api
    first = json.dumps({"assistant_id": str(aid)}).encode()
    busy = json.dumps({"assistant_id": str(aid), "multitask_strategy": "reject"}).encode()
    accepted = (await create(client, tid, first, key="first")).json()
    rejected = await create(client, tid, busy, key="busy")
    assert rejected.status_code == 200 and rejected.json()["result"] == "definitively_not_accepted"
    async with connect() as conn:
        (await conn.session.get(RunRow, UUID(accepted["run_id"]))).status = "success"
    assert (await create(client, tid, busy, key="busy")).json()[
        "result"
    ] == "definitively_not_accepted"
    assert (await read(client, tid, busy, key="busy")).json()["run_id"] is None
    bad = json.dumps({"assistant_id": str(uuid4())}).encode()
    assert (await create(client, tid, bad, key="bad")).status_code == 404
    assert (await read(client, tid, bad, key="bad")).json()["result"] == "unknown"
    assert len(enqueued) == 1


@pytest.mark.asyncio
async def test_failure_after_insert_rolls_back_and_enqueue_failure_keeps_acceptance(
    receipt_api, monkeypatch
):
    from langgraph_runtime_pg.auth import RuntimeContextError

    client, aid, tid, enqueued = receipt_api
    body = json.dumps({"assistant_id": str(aid)}).encode()
    original_sign = core_api.sign_runtime_context

    def fail(*args, **kwargs):
        raise RuntimeContextError("fixture failure")

    monkeypatch.setattr(core_api, "sign_runtime_context", fail)
    assert (await create(client, tid, body)).status_code == 503
    async with connect() as conn:
        assert await conn.session.scalar(select(func.count()).select_from(RunRow)) == 0
        assert await conn.session.scalar(select(func.count()).select_from(RunAcceptanceRow)) == 0
    assert (await read(client, tid, body)).json()["result"] == "unknown" and not enqueued
    monkeypatch.setattr(core_api, "sign_runtime_context", original_sign)

    async def broken_redis(run_id):
        raise RuntimeError("Redis unavailable")

    monkeypatch.setattr(core_api, "enqueue_run", broken_redis)
    accepted = await create(client, tid, body)
    assert accepted.json()["result"] == "accepted"
    assert (await read(client, tid, body)).json()["run_id"] == accepted.json()["run_id"]


@pytest.mark.asyncio
async def test_active_retention_expiry_and_permanent_tombstone(receipt_api):
    client, aid, tid, enqueued = receipt_api
    body = json.dumps({"assistant_id": str(aid)}).encode()
    created = (await create(client, tid, body)).json()
    repo = RunAcceptanceRepository()
    async with connect() as conn:
        row = await conn.session.scalar(select(RunAcceptanceRow))
        row.recorded_at -= timedelta(days=30)
        assert await repo.maintain_acceptance_receipts(conn.session) == 0
    assert (await read(client, tid, body)).json()["result"] == "accepted"
    async with connect() as conn:
        (await conn.session.get(RunRow, UUID(created["run_id"]))).status = "success"
    async with connect() as conn:
        assert await repo.maintain_acceptance_receipts(conn.session) == 1
        row = await conn.session.scalar(select(RunAcceptanceRow))
        expiry = row.details_expires_at
    await read(client, tid, body)
    async with connect() as conn:
        row = await conn.session.scalar(select(RunAcceptanceRow))
        assert row.details_expires_at == expiry
        row.details_expires_at -= timedelta(days=8)
    assert (await read(client, tid, body)).json()["reason"] == "receipt_expired"
    assert (await create(client, tid, body)).status_code == 409
    async with connect() as conn:
        assert await repo.maintain_acceptance_receipts(conn.session) == 1
        row = await conn.session.scalar(select(RunAcceptanceRow))
        assert (
            row.outcome == "expired" and row.authorization_metadata is None and row.run_id is None
        )
        await conn.session.execute(delete(RunRow))
        await conn.session.execute(delete(ThreadRow))
    assert (await read(client, tid, body)).json()["result"] == "unknown"
    assert (await create(client, tid, body)).status_code == 409 and len(enqueued) == 1


@pytest.mark.asyncio
async def test_uncommitted_receipt_is_unknown_then_accepted(receipt_api):
    client, aid, tid, _ = receipt_api
    body = json.dumps({"assistant_id": str(aid)}).encode()
    grant = AcceptanceGrant.parse(json.loads(headers(tid, body)["Authorization"])["grant"])
    repo = RunAcceptanceRepository()
    async with connect() as conn:
        from langgraph_runtime_pg.run_store import RunRepository

        await repo.acquire_key(conn.session, grant)
        run = await RunRepository().create(
            conn.session,
            assistant_id=aid,
            thread_id=tid,
            kwargs={},
            metadata={},
            idempotency_key="receipt:v1:" + grant.receipt_key,
        )
        await repo.record(conn.session, grant, {"owner": "alice"}, run)
        assert (await read(client, tid, body)).json()["result"] == "unknown"
    assert (await read(client, tid, body)).json()["run_id"] == str(run.run_id)


@pytest.mark.asyncio
async def test_schema_constraints_and_no_foreign_keys(pg_runtime):
    async with connect() as conn:
        assert (
            await conn.session.scalar(text("SELECT version_num FROM alembic_version"))
            == "015_checkpoint_journal"
        )
        assert (
            await conn.session.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint WHERE conrelid='run_acceptance_receipts'::regclass AND contype='f'"
                )
            )
            == 0
        )
    with pytest.raises(IntegrityError):
        async with connect() as conn:
            conn.session.add(
                RunAcceptanceRow(
                    receipt_key="0" * 64,
                    scope_id="1" * 64,
                    credential_id="2" * 64,
                    key_sha256="3" * 64,
                    thread_id=uuid4(),
                    request_digest=request_digest(b"{}"),
                    outcome="accepted",
                    authorization_metadata={},
                )
            )
            await conn.session.flush()


@pytest.mark.asyncio
async def test_reads_and_replays_preserve_business_rows(receipt_api):
    from langgraph_runtime_pg.database import get_session_factory

    client, aid, tid, enqueued = receipt_api
    body = json.dumps({"assistant_id": str(aid)}).encode()
    first = (await create(client, tid, body)).json()
    statements = []
    engine = get_session_factory().kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        for _ in range(10):
            assert (await read(client, tid, body)).json()["run_id"] == first["run_id"]
        assert not {"INSERT", "UPDATE", "DELETE", "SELECT_FOR_UPDATE"}.intersection(statements)
        statements.clear()
        for _ in range(3):
            replay = (await create(client, tid, body)).json()
            assert (
                replay["run_id"] == first["run_id"]
                and replay["accepted_at"] == first["accepted_at"]
            )
        assert not {"INSERT", "UPDATE", "DELETE"}.intersection(statements)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert enqueued == [first["run_id"]]


@pytest.mark.asyncio
async def test_commit_failure_is_not_acceptance(receipt_api, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    client, aid, tid, enqueued = receipt_api
    body = json.dumps({"assistant_id": str(aid)}).encode()
    original = AsyncSession.commit

    async def fail_commit(self):
        raise RuntimeError("fixture commit failed")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="fixture commit failed"):
        await create(client, tid, body)
    monkeypatch.setattr(AsyncSession, "commit", original)
    assert (await read(client, tid, body)).json()["result"] == "unknown"
    async with connect() as conn:
        assert await conn.session.scalar(select(func.count()).select_from(RunRow)) == 0
        assert await conn.session.scalar(select(func.count()).select_from(RunAcceptanceRow)) == 0
    assert not enqueued


@pytest.mark.asyncio
async def test_cross_thread_concurrency_and_missing_ledger(receipt_api):
    from langgraph_runtime_pg.run_store import RunRepository

    client, aid, tid, enqueued = receipt_api
    other = uuid4()
    async with connect() as conn:
        conn.session.add(ThreadRow(thread_id=other, metadata_={"owner": "alice"}))
    body = json.dumps({"assistant_id": str(aid)}).encode()
    outcomes = await asyncio.gather(create(client, tid, body), create(client, other, body))
    assert sorted(r.status_code for r in outcomes) == [200, 409]
    assert len(enqueued) == 1
    grant = AcceptanceGrant.parse(
        json.loads(headers(tid, body, key="broken")["Authorization"])["grant"]
    )
    async with connect() as conn:
        await RunRepository().create(
            conn.session,
            assistant_id=aid,
            thread_id=tid,
            kwargs={},
            metadata={},
            idempotency_key="receipt:v1:" + grant.receipt_key,
        )
    assert (await create(client, tid, body, key="broken")).status_code == 503
    assert (await read(client, tid, body, key="broken")).json()["result"] == "unknown"


@pytest.mark.asyncio
async def test_capacity_skip_locked_and_active_requeue(receipt_api):
    client, aid, tid, _ = receipt_api
    body = json.dumps({"assistant_id": str(aid)}).encode()
    first = (await create(client, tid, body)).json()
    async with connect() as conn:
        await conn.session.execute(
            text("""
            INSERT INTO run_acceptance_receipts
            (receipt_key,scope_id,credential_id,key_sha256,thread_id,request_digest,outcome,run_id,accepted_at,authorization_metadata,recorded_at,closed_observed_at,details_expires_at)
            SELECT lpad(to_hex(n),64,'0'), repeat('d',64), repeat('e',64), lpad(to_hex(n),64,'0'), :tid,
            :digest, CASE WHEN n<=4000 THEN 'accepted' ELSE 'expired' END,
            CASE WHEN n<=2000 THEN CAST(:rid AS uuid) WHEN n<=4000 THEN gen_random_uuid() ELSE NULL END,
            CASE WHEN n<=4000 THEN now() ELSE NULL END,
            CASE WHEN n<=4000 THEN '{}'::jsonb ELSE NULL END,
            now()-interval '30 days', NULL,
            CASE WHEN n>4000 THEN now()-interval '1 day' ELSE NULL END
            FROM generate_series(1,100000) n
        """),
            {"tid": tid, "rid": UUID(first["run_id"]), "digest": request_digest(body)},
        )
        await conn.session.execute(text("ANALYZE run_acceptance_receipts"))
        plan = await conn.session.scalar(
            text("""
            EXPLAIN (FORMAT JSON) SELECT receipt_key FROM run_acceptance_receipts
            WHERE outcome='accepted' AND closed_observed_at IS NULL ORDER BY recorded_at LIMIT 1000
        """)
        )
        assert "ix_run_acceptance_open" in json.dumps(plan)
    repo = RunAcceptanceRepository()

    async def maintain():
        async with connect() as conn:
            return await repo.maintain_acceptance_receipts(conn.session)

    counts = await asyncio.gather(maintain(), maintain())
    assert counts == [1000, 1000]
    async with connect() as conn:
        assert (
            await conn.session.scalar(
                select(func.count())
                .select_from(RunAcceptanceRow)
                .where(
                    RunAcceptanceRow.outcome == "accepted",
                    RunAcceptanceRow.closed_observed_at.is_not(None),
                )
            )
            == 2000
        )
        run = await conn.session.get(RunRow, UUID(first["run_id"]))
        run.status = "interrupted"
    assert await maintain() == 1000
    async with connect() as conn:
        run = await conn.session.get(RunRow, UUID(first["run_id"]))
        run.status = "pending"
        await conn.session.execute(
            text(
                "UPDATE run_acceptance_receipts SET details_expires_at=now()-interval '1 day' WHERE closed_observed_at IS NOT NULL"
            )
        )
    assert (await read(client, tid, body)).json()["result"] == "accepted"
    await maintain()
    async with connect() as conn:
        assert (
            await conn.session.scalar(
                select(func.count())
                .select_from(RunAcceptanceRow)
                .where(
                    RunAcceptanceRow.run_id == UUID(first["run_id"]),
                    RunAcceptanceRow.outcome == "accepted",
                )
            )
            == 2001
        )


@pytest.mark.asyncio
async def test_additive_012_migration_preserves_rows_and_refuses_downgrade(pg_runtime):
    import psycopg
    from alembic import command
    from psycopg import sql

    from langgraph_runtime_pg.database import to_psycopg_uri
    from langgraph_runtime_pg.migrate import alembic_config
    from test_persistence_contract import _schema_uri

    uri = to_psycopg_uri()
    schema = "receipt_migration_" + uuid4().hex[:12]
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        scoped = _schema_uri(uri, schema)
        cfg = alembic_config(scoped, version_table_schema=schema)
        command.upgrade(cfg, "012_run_callback_deliveries")
        aid, tid, rid = uuid4(), uuid4(), uuid4()
        with psycopg.connect(scoped) as conn:
            conn.execute(
                "INSERT INTO assistants(assistant_id,graph_id,name,version) VALUES(%s,'test','test',1)",
                (aid,),
            )
            conn.execute(
                "INSERT INTO threads(thread_id,status,metadata) VALUES(%s,'idle','{}')", (tid,)
            )
            conn.execute(
                "INSERT INTO runs(run_id,thread_id,assistant_id,status) VALUES(%s,%s,%s,'pending')",
                (rid, tid, aid),
            )
            before = conn.execute("SELECT row_to_json(runs)::text FROM runs").fetchall()
        command.upgrade(cfg, "013_run_acceptance_receipts")
        with psycopg.connect(scoped) as conn:
            assert conn.execute("SELECT row_to_json(runs)::text FROM runs").fetchall() == before
            assert conn.execute("SELECT count(*) FROM run_acceptance_receipts").fetchone() == (0,)
        with pytest.raises(RuntimeError, match="Retain acceptance"):
            command.downgrade(cfg, "012_run_callback_deliveries")
        with psycopg.connect(scoped) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "013_run_acceptance_receipts",
            )
    finally:
        with psycopg.connect(uri, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
