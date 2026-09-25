"""The public Auth contract must work without importing langgraph-api."""

import pytest
from langgraph_sdk import Auth
from sqlalchemy import column, select
from sqlalchemy.dialects.postgresql import JSONB, dialect
from starlette.exceptions import HTTPException

from langgraph_runtime_pg.authorization import authorize, metadata_predicate


@pytest.mark.asyncio
async def test_store_authorization_owns_namespace_without_builtin_scope(monkeypatch):
    from types import SimpleNamespace

    import httpx

    from langhost import server, store_api

    auth = Auth()
    calls = []

    @auth.authenticate
    async def authenticate(authorization):
        return {"identity": "alice"}

    @auth.on.store
    async def scope(ctx, value):
        calls.append(ctx.action)
        value["namespace"] = (ctx.user.identity, *(value.get("namespace") or ()))

    class Store:
        async def aget(self, namespace, key, **kwargs):
            assert namespace == ("alice", "notes")
            return SimpleNamespace(dict=lambda: {"namespace": namespace, "key": key, "value": {}})

        async def alist_namespaces(self, **kwargs):
            assert kwargs["prefix"] == ("alice",)
            return [("alice", "notes")]

    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    monkeypatch.setattr(store_api, "_store", Store)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/store/items?namespace=notes&key=one")
        assert response.status_code == 200, response.text
        assert response.json()["namespace"] == ["alice", "notes"]
        response = await client.post("/store/namespaces", json={})
        assert response.json() == {"namespaces": [["alice", "notes"]]}
    assert calls == ["get", "list_namespaces"]


@pytest.mark.asyncio
async def test_postgres_authorized_thread_pagination_and_mutations(monkeypatch):
    import os
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from langhost import core_api, server

    uri = os.environ.get("BOUNDARY_TEST_DATABASE_URI")
    if not uri:
        pytest.skip("Set BOUNDARY_TEST_DATABASE_URI to an isolated migrated PostgreSQL database")
    engine = create_async_engine(uri)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def connect():
        async with factory.begin() as session:
            yield SimpleNamespace(session=session)

    auth = Auth()
    group = uuid4().hex

    @auth.authenticate
    async def authenticate(authorization):
        return {"identity": authorization}

    @auth.on.threads
    async def resources(ctx, value):
        value.setdefault("metadata", {}).update(owner=ctx.user.identity, group=group)
        return {"owner": {"$eq": ctx.user.identity}, "group": group}

    monkeypatch.setattr(core_api, "connect", connect)
    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            alice = {"Authorization": "alice"}
            bob = {"Authorization": "bob"}
            response = await client.post(
                "/threads", json={"metadata": {"owner": "bob"}}, headers=alice
            )
            assert response.status_code == 200, response.text
            thread = response.json()
            assert thread["metadata"]["owner"] == "alice"
            assert (await client.post("/threads", json={}, headers=bob)).status_code == 200
            path = f"/threads/{thread['thread_id']}"
            assert (await client.get(path, headers=bob)).status_code == 404
            assert (
                await client.patch(path, json={"metadata": {"owner": "bob"}}, headers=bob)
            ).status_code == 404
            assert (await client.post("/threads/count", json={}, headers=alice)).json() == 1
            page = await client.post("/threads/search", json={"limit": 1}, headers=alice)
            assert [row["thread_id"] for row in page.json()] == [thread["thread_id"]]
            assert (
                await client.post("/threads/search", json={"ids": []}, headers=alice)
            ).json() == []
            assert (
                await client.post("/threads/count", json={"ids": []}, headers=alice)
            ).json() == 0
            updated = await client.patch(
                path, json={"metadata": {"title": "safe", "owner": "bob"}}, headers=alice
            )
            assert updated.status_code == 200 and updated.json()["metadata"]["owner"] == "alice"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_production_thread_endpoints_deny_before_database(monkeypatch):
    import httpx

    from langhost import server

    auth = Auth()
    events = []

    @auth.authenticate
    async def authenticate(authorization):
        return {"identity": "alice"}

    @auth.on.threads
    async def deny(ctx, value):
        events.append(ctx.action)
        return False

    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/threads", "/threads/search", "/threads/count"):
            response = await client.post(path, json={})
            assert response.status_code == 403, response.text
        response = await client.get("/threads/00000000-0000-0000-0000-000000000001")
        assert response.status_code == 403
    assert events == ["create", "search", "search", "read"]


@pytest.mark.asyncio
async def test_specific_handler_replaces_global_and_preserves_mutation():
    auth = Auth()
    calls = []

    @auth.on
    async def global_handler(ctx, value):
        calls.append("global")
        return False

    @auth.on.threads.create
    async def create(ctx, value):
        calls.append(ctx.user.identity)
        value.setdefault("metadata", {})["team"] = ctx.user["team"]
        return {"team": ctx.user.team}

    value = {}
    assert await authorize(
        auth, {"identity": "alice", "team": "blue"}, "threads", "create", value
    ) == {"team": "blue"}
    assert value == {"metadata": {"team": "blue"}}
    assert calls == ["alice"]
    with pytest.raises(HTTPException) as error:
        await authorize(auth, {"identity": "alice"}, "threads", "read", {})
    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,status", [(None, None), (True, None), ({}, None), (False, 403), ([], 500), (0, 500)]
)
async def test_authorization_result_contract(result, status):
    auth = Auth()

    @auth.on
    async def handler(ctx, value):
        return result

    if status is None:
        assert await authorize(auth, {"identity": "alice"}, "threads", "read", {}) == {}
    else:
        with pytest.raises(HTTPException) as error:
            await authorize(auth, {"identity": "alice"}, "threads", "read", {})
        assert error.value.status_code == status


@pytest.mark.parametrize(
    "filters",
    [
        {"owner": {"$unknown": "alice"}},
        {"$or": {}},
        {"$unknown": []},
        {"owner": {"$eq": "a", "$contains": "a"}},
    ],
)
def test_invalid_filters_fail_closed(filters):
    with pytest.raises(HTTPException):
        metadata_predicate(column("metadata", JSONB), filters)


def test_filters_compile_as_bound_json_before_pagination():
    metadata = column("metadata", JSONB)
    query = (
        select(metadata)
        .where(
            metadata_predicate(
                metadata,
                {
                    "owner": "alice",
                    "members": {"$contains": ["alice", "bob"]},
                },
            )
        )
        .limit(10)
    )
    compiled = query.compile(dialect=dialect())
    sql = str(compiled)
    assert "jsonb_typeof" in sql and "@>" in sql and "::JSONB" in sql
    assert "alice" not in sql
    assert sql.index("WHERE") < sql.index("LIMIT")
