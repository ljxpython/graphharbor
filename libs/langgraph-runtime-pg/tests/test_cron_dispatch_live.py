"""Cron contract against an explicitly named disposable PostgreSQL database."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from langgraph_sdk import Auth
from langgraph_sdk._async.client import LangGraphClient
from sqlalchemy import select


@pytest.mark.asyncio
async def test_cron_sdk_dispatch_worker_and_controls(tmp_path: Path) -> None:
    assert "graphharbor_cron_probe_20261005" in os.environ.get("DATABASE_URI", "")
    assert os.environ.get("GRAPHHARBOR_REDIS_PREFIX") == "graphharbor:cron-probe-20261005"

    from langgraph_runtime_pg.cron import dispatch_due_crons
    from langgraph_runtime_pg.database import connect, start_pool, truncate_all
    from langgraph_runtime_pg.models import CronRow, RunRow, ThreadRow
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langhost.server import create_app

    (tmp_path / "graph.py").write_text(
        "from typing_extensions import TypedDict\n"
        "from langgraph.graph import START, END, StateGraph\n"
        "class State(TypedDict):\n    value: int\n"
        "def inc(state: State):\n    return {'value': state['value'] + 1}\n"
        "g = StateGraph(State)\n"
        "g.add_node('inc', inc)\n"
        "g.add_edge(START, 'inc')\n"
        "g.add_edge('inc', END)\n"
        "graph = g.compile()\n"
    )
    app = create_app({"graphs": {"probe": "graph.py:graph"}}, base_dir=tmp_path)
    await start_pool()
    await truncate_all()
    async with (
        LifespanManager(app),
        LangGraphClient(
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        ) as client,
    ):
        assistant = await client.assistants.create("probe")
        reservation = str(uuid4())
        threads = await asyncio.gather(
            *[
                client.threads.create(thread_id=reservation, if_exists="do_nothing")
                for _ in range(3)
            ]
        )
        assert {thread["thread_id"] for thread in threads} == {reservation}
        cron = await client.crons.create(
            assistant["assistant_id"],
            schedule="* * * * *",
            input={"value": 1},
            metadata={"probe": "cron-contract"},
            on_run_completed="keep",
        )
        cron_id = cron["cron_id"]
        assert cron["next_run_date"] is not None
        assert await client.crons.count(assistant_id=assistant["assistant_id"]) == 1
        projected = await client.crons.search(
            assistant_id=assistant["assistant_id"],
            enabled=True,
            sort_by="created_at",
            sort_order="ASC",
            select=["cron_id", "schedule"],
        )
        assert projected == [{"cron_id": cron_id, "schedule": "* * * * *"}]
        changed = await client.crons.update(
            cron_id, schedule="*/5 * * * *", timezone="Asia/Shanghai"
        )
        assert changed["timezone"] == "Asia/Shanghai"
        assert datetime.fromisoformat(changed["next_run_date"]).minute % 5 == 0
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get(f"/runs/crons/{cron_id}")
            assert response.status_code == 200
            assert response.json()["cron_id"] == cron_id
        async with connect() as conn:
            row = await conn.session.get(CronRow, cron_id)
            row.next_run_date = datetime.now(UTC) - timedelta(seconds=1)
        claimed = await asyncio.gather(dispatch_due_crons(), dispatch_due_crons())
        assert sorted(claimed) == [0, 1]
        async with connect() as conn:
            runs = (await conn.session.execute(select(RunRow))).scalars().all()
            assert len(runs) == 1
            run = runs[0]
            assert run.metadata_["cron_id"] == cron_id
            assert run.kwargs["config"]["configurable"]["cron_id"] == cron_id
            assert run.thread_id is not None
            run_id, thread_id = run.run_id, run.thread_id
        worker = ProductionWorker(app.state.graph_registry, owner="cron-contract")
        assert await worker.run_once()
        async with connect() as conn:
            run = await conn.session.get(RunRow, run_id)
            thread = await conn.session.get(ThreadRow, thread_id)
            assert run.status == "success"
            assert thread.values_["value"] == 2
        paused = await client.crons.update(cron_id, enabled=False)
        assert paused["enabled"] is False
        async with connect() as conn:
            row = await conn.session.get(CronRow, cron_id)
            row.next_run_date = datetime.now(UTC) - timedelta(seconds=1)
        assert await dispatch_due_crons() == 0
        await client.crons.delete(cron_id)
        assert all(item["cron_id"] != cron_id for item in await client.crons.search())

        once_at = (datetime.now(UTC) + timedelta(seconds=3)).replace(microsecond=0)
        finite = await client.crons.create(
            assistant["assistant_id"],
            schedule=f"{once_at.minute} {once_at.hour} {once_at.day} {once_at.month} * {once_at.second} {once_at.year}",
            timezone="UTC",
            input={"value": 6},
            on_run_completed="keep",
        )
        await asyncio.sleep(max(0, (once_at - datetime.now(UTC)).total_seconds()) + 0.05)
        await dispatch_due_crons()
        assert await dispatch_due_crons() == 0
        async with connect() as conn:
            finite_row = await conn.session.get(CronRow, finite["cron_id"])
            assert finite_row.enabled is False and finite_row.next_run_date is None
            finite_run = await conn.session.scalar(
                select(RunRow).where(RunRow.metadata_["cron_id"].astext == finite["cron_id"])
            )
            assert finite_run is not None
            from langgraph_runtime_pg.run_store import RunRepository

            rows, total = await RunRepository().search(
                conn.session, metadata={"cron_id": finite["cron_id"]}, limit=1, offset=0
            )
            assert total == 1 and rows[0].run_id == finite_run.run_id
            assert (
                await RunRepository().search(
                    conn.session, metadata={"cron_id": finite["cron_id"]}, limit=1, offset=1
                )
            ) == ([], 1)
            assert (
                await RunRepository().search(
                    conn.session,
                    metadata={"cron_id": finite["cron_id"], "tenant": "other"},
                    limit=1,
                    offset=0,
                )
            ) == ([], 0)
        assert await worker.run_once()
        async with connect() as conn:
            assert (await conn.session.get(RunRow, finite_run.run_id)).status == "success"
        await client.crons.delete(finite["cron_id"])

        default_cron = await client.crons.create(
            assistant["assistant_id"], schedule="* * * * *", input={"value": 3}
        )
        default_id = default_cron["cron_id"]
        async with connect() as conn:
            row = await conn.session.get(CronRow, default_id)
            row.next_run_date = datetime.now(UTC) - timedelta(seconds=1)
        assert await dispatch_due_crons() == 1
        assert await worker.run_once()
        async with connect() as conn:
            run = await conn.session.scalar(
                select(RunRow).where(RunRow.metadata_["cron_id"].astext == default_id)
            )
            assert run is not None and run.status == "success"
            assert run.thread_id is None
        await client.crons.update(default_id, enabled=False)
        await client.crons.delete(default_id)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            for body in (
                {"schedule": "bad expression"},
                {"schedule": "* * * * *", "timezone": "Bad/Zone"},
                {"schedule": "* * * * *", "end_time": "not-a-date"},
            ):
                response = await http.post(
                    "/runs/crons", json={"assistant_id": assistant["assistant_id"], **body}
                )
                assert response.status_code == 422

        expired = await client.crons.create(
            assistant["assistant_id"],
            schedule="* * * * *",
            input={"value": 4},
            end_time=datetime.now(UTC) - timedelta(seconds=10),
        )
        async with connect() as conn:
            row = await conn.session.get(CronRow, expired["cron_id"])
            row.next_run_date = datetime.now(UTC) - timedelta(seconds=20)
        assert await dispatch_due_crons() == 0
        await client.crons.delete(expired["cron_id"])

        cutoff = await client.crons.create(
            assistant["assistant_id"], schedule="* * * * *", input={"value": 4}
        )
        await client.crons.update(
            cutoff["cron_id"], end_time=datetime.now(UTC) - timedelta(seconds=10)
        )
        async with connect() as conn:
            row = await conn.session.get(CronRow, cutoff["cron_id"])
            row.next_run_date = datetime.now(UTC) - timedelta(seconds=20)
        assert await dispatch_due_crons() == 0
        await client.crons.delete(cutoff["cron_id"])

        retry = await client.crons.create(
            assistant["assistant_id"], schedule="* * * * *", input={"value": 5}
        )
        async with connect() as conn:
            row = await conn.session.get(CronRow, retry["cron_id"])
            row.next_run_date = datetime.now(UTC) - timedelta(seconds=1)
        with patch(
            "langgraph_runtime_pg.cron.RunRepository.create", side_effect=RuntimeError("probe")
        ):
            assert await dispatch_due_crons() == 0
        async with connect() as conn:
            row = await conn.session.get(CronRow, retry["cron_id"])
            assert row.next_run_date < datetime.now(UTC)
        assert await dispatch_due_crons() == 1
        await client.crons.delete(retry["cron_id"])

        restart = await client.crons.create(
            assistant["assistant_id"], schedule="* * * * *", input={"value": 6}
        )
        restart_id = restart["cron_id"]
    restarted_app = create_app({"graphs": {"probe": "graph.py:graph"}}, base_dir=tmp_path)
    async with LifespanManager(restarted_app):
        async with connect() as conn:
            row = await conn.session.get(CronRow, restart_id)
            row.next_run_date = datetime.now(UTC) - timedelta(seconds=1)
        for _ in range(30):
            async with connect() as conn:
                recovered = await conn.session.scalar(
                    select(RunRow).where(RunRow.metadata_["cron_id"].astext == restart_id)
                )
            if recovered is not None:
                break
            await asyncio.sleep(0.1)
        assert recovered is not None
        async with LangGraphClient(
            AsyncClient(transport=ASGITransport(app=restarted_app), base_url="http://test")
        ) as client:
            await client.crons.update(restart_id, enabled=False)
            await client.crons.delete(restart_id)


def test_cron_timezone_dst_transition() -> None:
    from langgraph_runtime_pg.cron import next_cron_date

    before = datetime(2026, 3, 8, 6, 55, tzinfo=UTC)
    next_run = next_cron_date("0 3 * * *", before, "America/New_York")
    assert next_run == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_cron_production_identity_and_tenant_scope(tmp_path: Path, monkeypatch) -> None:
    assert "graphharbor_cron_probe_20261005" in os.environ.get("DATABASE_URI", "")
    assert os.environ.get("GRAPHHARBOR_REDIS_PREFIX") == "graphharbor:cron-probe-20261005"
    from langgraph_runtime_pg.auth import verify_runtime_context
    from langgraph_runtime_pg.cron import dispatch_due_crons
    from langgraph_runtime_pg.database import connect, start_pool, truncate_all
    from langgraph_runtime_pg.models import CronRow, RunRow
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langhost import server

    monkeypatch.setenv("GRAPHHARBOR_ENV", "production")
    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_SECRET", "cron-isolation-test-secret")
    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_ISSUER", "cron-test")
    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_AUDIENCE", "cron-worker")
    revoked = set()
    revision = {"value": "initial"}
    auth = Auth()

    @auth.authenticate
    async def authenticate(authorization):
        identity = (authorization or "").removeprefix("Bearer ")
        if identity in revoked or identity not in {"alice", "bob"}:
            raise Auth.exceptions.HTTPException(status_code=401, detail="revoked")
        return {"identity": identity, "tenant": identity, "revision": revision["value"]}

    @auth.on.crons.create
    async def scope_cron_create(ctx, value):
        value["metadata"] = {**value.get("metadata", {}), "tenant": ctx.user.identity}

    @auth.on.crons.search
    async def scope_cron_search(ctx, value):
        return {"tenant": ctx.user.identity}

    @auth.on.crons.read
    async def scope_cron_read(ctx, value):
        return {"tenant": ctx.user.identity}

    @auth.on.crons.update
    async def scope_cron_update(ctx, value):
        return {"tenant": ctx.user.identity}

    @auth.on.crons.delete
    async def scope_cron_delete(ctx, value):
        return {"tenant": ctx.user.identity}

    (tmp_path / "graph.py").write_text(
        "from langgraph.graph import START, END, StateGraph\n"
        "from typing_extensions import TypedDict\n"
        "class State(TypedDict):\n    value: int\n"
        "def inc(state: State):\n    return {'value': state['value'] + 1}\n"
        "g = StateGraph(State)\n"
        "g.add_node('inc', inc)\n"
        "g.add_edge(START, 'inc')\n"
        "g.add_edge('inc', END)\n"
        "graph = g.compile()\n"
    )
    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    app = server.create_app(
        {"graphs": {"probe": "graph.py:graph"}, "auth": {"path": "fixture:auth"}},
        base_dir=tmp_path,
    )
    await start_pool()
    await truncate_all()
    async with LifespanManager(app):  # noqa: SIM117 - preserve separate HTTP client lifecycle
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            alice = {"Authorization": "Bearer alice"}
            bob = {"Authorization": "Bearer bob"}
            assistant = await http.post("/assistants", json={"graph_id": "probe"}, headers=alice)
            assert assistant.status_code in {200, 201}, assistant.text
            cron = await http.post(
                "/runs/crons",
                json={
                    "assistant_id": assistant.json()["assistant_id"],
                    "schedule": "* * * * *",
                    "input": {"value": 1},
                },
                headers=alice,
            )
            assert cron.status_code == 200, cron.text
            cron_id = cron.json()["cron_id"]
            assert cron.json()["metadata"]["tenant"] == "alice"
            assert "__graphharbor_runtime_context" not in cron.json()["payload"]
            assert (await http.get(f"/runs/crons/{cron_id}", headers=bob)).status_code == 404
            assert (
                await http.patch(f"/runs/crons/{cron_id}", json={"enabled": False}, headers=bob)
            ).status_code == 404
            assert (await http.post("/runs/crons/search", json={}, headers=bob)).json() == []
            assert (await http.delete(f"/runs/crons/{cron_id}", headers=bob)).status_code == 204
            assert (await http.get(f"/runs/crons/{cron_id}", headers=alice)).status_code == 200
            revision["value"] = "edited"
            changed = await http.patch(
                f"/runs/crons/{cron_id}", json={"input": {"value": 5}}, headers=alice
            )
            assert changed.status_code == 200, changed.text
            revision["value"] = "management-only"
            assert (
                await http.patch(f"/runs/crons/{cron_id}", json={"enabled": False}, headers=alice)
            ).status_code == 200
            assert (
                await http.patch(f"/runs/crons/{cron_id}", json={"enabled": True}, headers=alice)
            ).status_code == 200
            revoked.add("alice")
            assert (await http.get(f"/runs/crons/{cron_id}", headers=alice)).status_code == 401
            async with connect() as conn:
                row = await conn.session.get(CronRow, cron_id)
                row.next_run_date = datetime.now(UTC) - timedelta(seconds=1)
            assert await dispatch_due_crons() == 1
            async with connect() as conn:
                run = await conn.session.scalar(
                    select(RunRow).where(RunRow.metadata_["cron_id"].astext == cron_id)
                )
                assert run is not None
                assert run.metadata_["tenant"] == "alice"
                token = run.kwargs["runtime_context_token"]
                context = verify_runtime_context(token, run_id=str(run.run_id), thread_id=None)
                assert context["auth_user"]["identity"] == "alice"
                assert context["auth_user"]["revision"] == "edited"
                assert "__graphharbor_runtime_context" not in run.kwargs
            assert await ProductionWorker(app.state.graph_registry, owner="cron-auth").run_once()
            assert (await http.post("/runs/crons/search", json={}, headers=bob)).json() == []
            async with connect() as conn:
                run = await conn.session.get(RunRow, run.run_id)
                assert run.status == "success"
