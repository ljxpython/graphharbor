"""Real PostgreSQL CAS and HTTP tests in a fresh native temporary cluster."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.routing import Route

from langgraph_runtime_pg.models import ThreadRow
from langgraph_runtime_pg.thread_metadata import patch_thread_metadata
from langhost import core_api


@pytest.fixture(scope="module")
def isolated_pg(tmp_path_factory):
    root = tmp_path_factory.mktemp("metadata-cas-pg")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    subprocess.run(
        [
            "initdb",
            "-D",
            str(root / "pg"),
            "-U",
            "cas_test",
            "-A",
            "trust",
            "--no-locale",
            "-E",
            "UTF8",
        ],
        check=True,
        capture_output=True,
    )
    dsn = f"postgresql://cas_test@127.0.0.1:{port}/postgres"
    with (root / "postgres.log").open("w") as log:
        process = subprocess.Popen(
            ["postgres", "-D", str(root / "pg"), "-h", "127.0.0.1", "-p", str(port), "-k", ""],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while True:
                try:
                    with psycopg.connect(dsn, connect_timeout=1):
                        break
                except psycopg.OperationalError:
                    assert process.poll() is None and time.monotonic() < deadline
                    time.sleep(0.1)
            yield dsn.replace("postgresql://", "postgresql+asyncpg://")
        finally:
            process.terminate()
            process.wait(timeout=15)


@pytest.fixture
async def sessions(isolated_pg):
    engine = create_async_engine(isolated_pg)
    async with engine.begin() as conn:
        await conn.run_sync(ThreadRow.__table__.create, checkfirst=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def create_thread(sessions, metadata):
    tid = uuid4()
    async with sessions.begin() as session:
        session.add(ThreadRow(thread_id=tid, graph_id="test", metadata_=metadata))
    return tid


async def patch(sessions, tid, metadata, condition=None):
    async with sessions.begin() as session:
        row = await patch_thread_metadata(session, tid, metadata, if_metadata=condition)
        return dict(row.metadata_) if row is not None else None


@pytest.mark.asyncio
async def test_both_rename_orders_and_same_name_clear_condition(sessions):
    for first in ("rename", "auto", "same-name"):
        tid = await create_thread(sessions, {"title": "seed", "marker": "seed", "access": "keep"})
        expected = {"title": "seed", "marker": "seed"}
        if first != "auto":
            await patch(
                sessions,
                tid,
                {"title": "seed" if first == "same-name" else "manual", "marker": None},
            )
            with pytest.raises(HTTPException) as exc:
                await patch(sessions, tid, {"title": "candidate", "marker": None}, expected)
            assert exc.value.status_code == 409
        else:
            await patch(sessions, tid, {"title": "candidate", "marker": None}, expected)
            assert (await patch(sessions, tid, {"title": "manual", "marker": None}))[
                "title"
            ] == "manual"
        assert (await patch(sessions, tid, {"preview": "summary"}))["access"] == "keep"


@pytest.mark.asyncio
async def test_concurrent_sessions_have_one_winner_and_keep_unrelated_fields(sessions):
    tid = await create_thread(sessions, {"title": "seed", "marker": "seed", "acl": {"owner": "a"}})
    ready = asyncio.Event()
    waiting = 0

    async def compete(candidate):
        nonlocal waiting
        async with sessions.begin() as session:
            waiting += 1
            if waiting == 2:
                ready.set()
            await ready.wait()
            try:
                row = await patch_thread_metadata(
                    session,
                    tid,
                    {"title": candidate, "marker": None},
                    if_metadata={"title": "seed", "marker": "seed"},
                )
                return row.metadata_["title"]
            except HTTPException as exc:
                return exc.status_code

    results = await asyncio.gather(compete("a"), compete("b"))
    assert results.count(409) == 1
    await asyncio.gather(*(patch(sessions, tid, {f"field_{i}": i}) for i in range(8)))
    current = await patch(sessions, tid, {})
    assert current["acl"] == {"owner": "a"}
    assert all(current[f"field_{i}"] == i for i in range(8))


@pytest.mark.asyncio
async def test_null_missing_and_invisible_conditions(sessions):
    tid = await create_thread(sessions, {"title": "old", "project_id": "allowed"})
    for _ in range(2):
        assert (await patch(sessions, tid, {"missing": None}, {"missing": None}))["missing"] is None
    async with sessions.begin() as session:
        assert (
            await patch_thread_metadata(
                session,
                tid,
                {"title": "attack"},
                if_metadata={"title": "old"},
                filters={"project_id": "other"},
            )
            is None
        )
    assert (await patch(sessions, tid, {}))["title"] == "old"
    for condition in ({}, [], {"": "old"}, {"x" * 257: "old"}):
        with pytest.raises(HTTPException) as exc:
            await patch(sessions, tid, {}, condition)
        assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_http_contract_commits_checks_filters_and_returns_real_metadata(
    sessions, monkeypatch
):
    tid = await create_thread(
        sessions, {"title": "seed", "marker": "seed", "project_id": "allowed"}
    )

    @asynccontextmanager
    async def connect():
        async with sessions.begin() as session:
            yield SimpleNamespace(session=session)

    async def authorize(request, resource, action, value):
        assert (resource, action) == ("threads", "update")
        return {"project_id": request.headers.get("project", "allowed")}

    monkeypatch.setattr(core_api, "connect", connect)
    monkeypatch.setattr(core_api, "_authorize", authorize)
    app = Starlette(
        routes=[
            Route(
                "/threads/{thread_id}/metadata/cas",
                core_api.threads_metadata_cas,
                methods=["PATCH"],
            ),
            Route("/threads/{thread_id}", core_api.threads_update, methods=["PATCH"]),
        ]
    )
    path = f"/threads/{tid}/metadata/cas"
    body = {
        "metadata": {"title": "candidate", "marker": None},
        "if_metadata": {"title": "seed", "marker": "seed"},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for invalid in (
            {"metadata": {}},
            {"metadata": {}, "if_metadata": None},
            {"metadata": {}, "if_metadata": {}},
            {**body, "unknown": True},
        ):
            assert (await client.patch(path, json=invalid)).status_code == 422
        assert (
            await client.patch(path, json=body, headers={"project": "other"})
        ).status_code == 404
        response = await client.patch(path, json=body)
        assert response.status_code == 200
        assert response.json()["metadata"] == {
            "title": "candidate",
            "marker": None,
            "project_id": "allowed",
        }
        assert (await client.patch(path, json=body)).status_code == 409
        assert (await client.patch(f"/threads/{tid}", json={"ttl": None})).status_code == 200
        assert (
            await client.patch(
                f"/threads/{tid}",
                json={"metadata": {"title": "manual"}},
                headers={"Prefer": "return=minimal"},
            )
        ).status_code == 204
    assert (await patch(sessions, tid, {}))["title"] == "manual"
