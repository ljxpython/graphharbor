"""Real PostgreSQL retention, fencing and restart-safe application handoffs."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from starlette.applications import Starlette
from starlette.routing import Route

from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.models import CronRow, RunRow, ThreadDeletionRow, ThreadRow
from langgraph_runtime_pg.retention import (
    RetentionConflict,
    advance_deletion,
    configure_retention,
    maintain_retention,
    request_deletion,
    set_policy,
    touch,
    validate_policy,
)
from langgraph_runtime_pg.run_store import RunConflictError, RunRepository


@pytest.fixture(autouse=True)
def clear_hooks():
    configure_retention({}, lambda _: None)
    yield
    configure_retention({}, lambda _: None)


async def make_thread(*, expired=False):
    async with connect() as conn:
        row = ThreadRow(thread_id=uuid4(), status="idle", metadata_={"project_id": "p"})
        set_policy(row, {"mode": "idle", "ttl_minutes": 1})
        if expired:
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        conn.session.add(row)
        await conn.session.flush()
        return row.thread_id


def test_policy_validation():
    for invalid in (
        {},
        {"mode": "idle", "ttl_minutes": True},
        {"mode": "other", "ttl_minutes": 1},
        {"mode": "idle", "ttl_minutes": -1},
    ):
        with pytest.raises(ValueError):
            validate_policy(invalid)
    assert validate_policy(None) is None


@pytest.mark.parametrize("keys", [None, "project_id", [""], ["$or"], [1], [[]], ["p", "p"]])
def test_tombstone_metadata_keys_validation(keys):
    with pytest.raises(ValueError, match="tombstone_metadata_keys"):
        configure_retention({"thread_lifecycle": {"tombstone_metadata_keys": keys}}, lambda _: None)


async def test_completed_tombstone_drops_payload_and_preserves_authorization(
    pg_runtime, monkeypatch
):
    from langhost import core_api

    ready = False
    metadata = {"project_id": "p", "title": "private title", "credential": "test-secret"}

    async def after(event):
        assert event["metadata"] == metadata
        return ready

    config = {
        "thread_lifecycle": {"after_delete": "after", "tombstone_metadata_keys": ["project_id"]}
    }
    configure_retention(config, lambda _: after)
    tid = await make_thread(expired=True)
    async with connect() as conn:
        row = await conn.session.get(ThreadRow, tid)
        row.metadata_ = metadata
    assert (await maintain_retention(apply=True))["completed"] == 0
    async with connect() as conn:
        job = await conn.session.get(ThreadDeletionRow, tid)
        assert job.metadata_ == metadata and job.state == "after_delete"
        job.next_attempt_at = datetime.now(UTC)
    configure_retention({"thread_lifecycle": {"after_delete": "after"}}, lambda _: after)
    assert not await advance_deletion(tid)
    async with connect() as conn:
        job = await conn.session.get(ThreadDeletionRow, tid)
        assert job.metadata_ == metadata
        job.next_attempt_at = datetime.now(UTC)
    ready = True
    configure_retention(config, lambda _: after)
    assert await advance_deletion(tid)
    async with connect() as conn:
        assert (await conn.session.get(ThreadDeletionRow, tid)).metadata_ == {"project_id": "p"}

    async def scoped(request, *_args):
        return {"project_id": request.headers["x-project"]}

    monkeypatch.setattr(core_api, "_authorize", scoped)
    app = Starlette(
        routes=[
            Route("/threads/{thread_id}", core_api.threads_delete, methods=["DELETE"]),
            Route("/threads/{thread_id}/deletion", core_api.threads_deletion),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://retention"
    ) as client:
        for project, status in (("other", 404), ("p", 200)):
            response = await client.get(f"/threads/{tid}/deletion", headers={"x-project": project})
            assert response.status_code == status
            response = await client.delete(f"/threads/{tid}", headers={"x-project": project})
            assert response.status_code == (204 if project == "p" else 404)


async def test_preview_touch_and_fixed_expiry(pg_runtime):
    tid = await make_thread(expired=True)
    report = await maintain_retention()
    assert report["due_threads"] == [str(tid)]
    async with connect() as conn:
        assert await conn.session.get(ThreadDeletionRow, tid) is None
        row = await conn.session.get(ThreadRow, tid)
        touch(row)
        assert row.expires_at > datetime.now(UTC)
        set_policy(row, {"mode": "fixed", "ttl_minutes": 2})
        expires = row.expires_at
        touch(row)
        assert row.expires_at == expires
    assert not (await maintain_retention(apply=True))["due_threads"]


async def test_retention_read_has_distinct_authorization_scope(pg_runtime, monkeypatch):
    from starlette.exceptions import HTTPException

    from langhost import core_api

    tid = await make_thread()

    async def maintenance_only(request, resource, action, value):
        if (
            resource != "threads"
            or action != "read"
            or value
            != {
                "thread_id": tid,
                "retention": True,
            }
        ):
            raise HTTPException(403, "Retention scope required")
        return {}

    monkeypatch.setattr(core_api, "_authorize", maintenance_only)
    app = Starlette(
        routes=[
            Route("/threads/{thread_id}", core_api.threads_get),
            Route("/threads/{thread_id}/retention", core_api.threads_retention),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://retention"
    ) as client:
        assert (await client.get(f"/threads/{tid}/retention")).status_code == 200
        assert (await client.get(f"/threads/{tid}")).status_code == 403


async def test_atomic_delete_checkpoints_and_tombstone(pg_runtime):
    from langgraph.checkpoint.base import empty_checkpoint

    from langgraph_runtime_pg.checkpoint import get_checkpointer

    tid = await make_thread(expired=True)
    cp = empty_checkpoint()
    await get_checkpointer().aput(
        {"configurable": {"thread_id": str(tid), "checkpoint_ns": ""}},
        cp,
        {"source": "input", "step": -1, "parents": {}},
        {},
    )
    assert (await maintain_retention(apply=True))["completed"] == 1
    async with connect() as conn:
        assert await conn.session.get(ThreadRow, tid) is None
        assert (await conn.session.get(ThreadDeletionRow, tid)).state == "completed"
        assert not await conn.session.scalar(
            text("SELECT count(*) FROM checkpoints WHERE thread_id=:id"), {"id": str(tid)}
        )
        with pytest.raises(RunConflictError):
            await RunRepository().create(
                conn.session, assistant_id=uuid4(), thread_id=tid, kwargs={}, metadata={}
            )
    with pytest.raises(ValueError, match="deletion"):
        await get_checkpointer().aput(
            {"configurable": {"thread_id": str(tid), "checkpoint_ns": ""}},
            cp,
            {"source": "input", "step": -1, "parents": {}},
            {},
        )


async def test_pending_and_cron_protected(pg_runtime):
    tid = await make_thread(expired=True)
    async with connect() as conn:
        run = await RunRepository().create(
            conn.session, assistant_id=uuid4(), thread_id=tid, kwargs={}, metadata={}
        )
        run_id = run.run_id
    assert (await maintain_retention(apply=True))["completed"] == 0
    async with connect() as conn:
        assert await conn.session.get(ThreadDeletionRow, tid) is None
        await conn.session.delete(await conn.session.get(RunRow, run_id))
        conn.session.add(
            CronRow(
                cron_id=uuid4(),
                thread_id=tid,
                assistant_id=uuid4(),
                schedule="* * * * *",
                payload={},
                metadata_={},
                next_run_date=datetime.now(UTC),
            )
        )
    assert (await maintain_retention(apply=True))["completed"] == 0


async def test_handoff_failure_missing_config_and_restart(pg_runtime):
    seen = []

    async def before(event):
        seen.append(("before", event["task_id"]))
        return True

    async def after(event):
        assert event["state"] == "after_delete"
        seen.append(("after", event["task_id"]))
        raise RuntimeError("sensitive application exception")

    hooks = {"before": before, "after": after}
    config = {"thread_lifecycle": {"before_delete": "before", "after_delete": "after"}}
    configure_retention(config, hooks.__getitem__)
    tid = await make_thread(expired=True)
    assert (await maintain_retention(apply=True))["completed"] == 0
    async with connect() as conn:
        job = await conn.session.get(ThreadDeletionRow, tid)
        assert job.state == "after_delete" and job.last_error == "RuntimeError"
        assert await conn.session.get(ThreadRow, tid) is None
        job.next_attempt_at = datetime.now(UTC)
    configure_retention({}, lambda _: None)
    assert not await advance_deletion(tid)
    async with connect() as conn:
        job = await conn.session.get(ThreadDeletionRow, tid)
        job.next_attempt_at = datetime.now(UTC)

    async def success(event):
        seen.append(("after_ok", event["task_id"]))

    hooks["after"] = success
    configure_retention(config, hooks.__getitem__)
    assert await advance_deletion(tid)
    assert [name for name, _ in seen] == ["before", "after", "after_ok"]
    assert len({task for _, task in seen}) == 1


async def test_copy_and_prune_cannot_mutate_deleting_thread(pg_runtime):
    from langhost import core_api

    tid = await make_thread()
    app = Starlette(
        routes=[
            Route("/threads/{thread_id}/copy", core_api.threads_copy, methods=["POST"]),
            Route("/threads/prune", core_api.threads_prune, methods=["POST"]),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://retention"
    ) as client:
        copied = await client.post(f"/threads/{tid}/copy")
        assert copied.status_code == 201
        assert copied.json()["retention"]["policy"] == {"mode": "idle", "ttl_minutes": 1}
        async with connect() as conn:
            row = await conn.session.get(ThreadRow, tid, with_for_update=True)
            await request_deletion(conn.session, row)
        assert (await client.post(f"/threads/{tid}/copy")).status_code == 409
        assert (
            await client.post(
                "/threads/prune",
                json={
                    "thread_ids": [str(tid)],
                    "strategy": "keep_latest",
                },
            )
        ).status_code == 409


async def test_touch_vs_mark_and_single_claim(pg_runtime):
    tid = await make_thread(expired=True)
    async with connect() as conn:
        row = await conn.session.scalar(
            select(ThreadRow).where(ThreadRow.thread_id == tid).with_for_update()
        )
        await request_deletion(conn.session, row)
        with pytest.raises(RetentionConflict):
            touch(row)
    results = await asyncio.gather(*(advance_deletion(tid) for _ in range(4)))
    assert any(results)
    async with connect() as conn:
        assert (await conn.session.get(ThreadDeletionRow, tid)).attempts == 1


async def test_expired_claim_recovers_and_protected_rows_do_not_starve(pg_runtime):
    protected = await make_thread(expired=True)
    due = await make_thread(expired=True)
    async with connect() as conn:
        conn.session.add(
            CronRow(
                cron_id=uuid4(),
                thread_id=protected,
                assistant_id=uuid4(),
                schedule="* * * * *",
                payload={},
                metadata_={},
                next_run_date=datetime.now(UTC),
            )
        )
    assert (await maintain_retention(limit=1))["due_threads"] == [str(due)]
    async with connect() as conn:
        row = await conn.session.get(ThreadRow, due, with_for_update=True)
        job = await request_deletion(conn.session, row)
        job.lease_owner = "dead-worker"
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await advance_deletion(due)
    async with connect() as conn:
        assert await conn.session.get(ThreadRow, protected) is not None


async def test_additive_migration_preserves_history_and_refuses_destructive_rollback(pg_runtime):
    import psycopg
    from alembic import command
    from psycopg import sql

    from langgraph_runtime_pg.database import to_psycopg_uri
    from langgraph_runtime_pg.migrate import alembic_config
    from test_persistence_contract import _schema_uri

    uri = to_psycopg_uri()
    schema = "retention_migration_" + uuid4().hex[:12]
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        scoped = _schema_uri(uri, schema)
        cfg = alembic_config(scoped, version_table_schema=schema)
        command.upgrade(cfg, "013_run_acceptance_receipts")
        tid = uuid4()
        with psycopg.connect(scoped) as conn:
            conn.execute(
                "INSERT INTO threads(thread_id,status,metadata) VALUES(%s,'idle','{\"old\":true}')",
                (tid,),
            )
        command.upgrade(cfg, "head")
        with psycopg.connect(scoped) as conn:
            assert conn.execute(
                "SELECT metadata,retention,last_activity_at,expires_at FROM threads WHERE thread_id=%s",
                (tid,),
            ).fetchone() == ({"old": True}, None, None, None)
        command.check(cfg)
        with pytest.raises(RuntimeError, match="forward-only"):
            command.downgrade(cfg, "013_run_acceptance_receipts")
    finally:
        with psycopg.connect(uri, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_http_retention_auth_and_get_not_touch(pg_runtime, monkeypatch):
    from langhost import core_api

    async def authorize(request, resource, action, value, **kwargs):
        return {"project_id": request.headers.get("x-project", "p")}

    monkeypatch.setattr(core_api, "_authorize", authorize)
    app = Starlette(
        routes=[
            Route("/threads", core_api.threads_create, methods=["POST"]),
            Route("/threads/{thread_id}", core_api.threads_get),
            Route("/threads/{thread_id}", core_api.threads_delete, methods=["DELETE"]),
            Route("/threads/{thread_id}/touch", core_api.threads_touch, methods=["POST"]),
            Route(
                "/threads/{thread_id}/retention",
                core_api.threads_retention,
                methods=["GET", "PATCH"],
            ),
            Route("/threads/{thread_id}/deletion", core_api.threads_deletion),
        ]
    )
    tid = await make_thread()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://retention"
    ) as client:
        policy = {"policy": {"mode": "idle", "ttl_minutes": 43200}}
        first = await client.patch(f"/threads/{tid}/retention", json=policy)
        assert first.status_code == 200
        assert (await client.get(f"/threads/{tid}/retention")).json() == first.json()
        assert (
            await client.post(f"/threads/{tid}/touch", headers={"x-project": "other"})
        ).status_code == 404
        assert (await client.post(f"/threads/{tid}/touch")).json()[
            "last_activity_at"
        ] > first.json()["last_activity_at"]
        assert (await client.delete(f"/threads/{tid}")).status_code == 204
        assert (await client.delete(f"/threads/{tid}")).status_code == 204
        assert (
            await client.get(f"/threads/{tid}/deletion", headers={"x-project": "other"})
        ).status_code == 404
        assert (await client.post("/threads", json={"thread_id": str(tid)})).status_code == 409
        assert (
            await client.post("/threads", json={"ttl": {"strategy": "keep_latest", "ttl": 1}})
        ).status_code == 422
