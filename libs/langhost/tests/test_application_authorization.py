"""The public Auth contract must work without importing langgraph-api."""

import pytest
from langgraph_sdk import Auth
from sqlalchemy import column, select
from sqlalchemy.dialects.postgresql import JSONB, dialect
from starlette.exceptions import HTTPException

from langgraph_runtime_pg.authorization import authorize, metadata_predicate


def test_custom_base_user_fields_survive_runtime_identity_snapshot():
    from langgraph_runtime_pg.auth import Principal

    class User:
        identity = "alice"

        def keys(self):
            return ("identity", "team", "permissions")

        def __getitem__(self, key):
            return {"identity": "alice", "team": "blue", "permissions": ["run"]}[key]

    principal = Principal.from_auth_user(User())
    assert principal.auth_user == {
        "identity": "alice", "team": "blue", "permissions": ["run"]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 503])
async def test_authentication_preserves_explicit_error_status(monkeypatch, status):
    import httpx

    from langhost import server

    auth = Auth()

    @auth.authenticate
    async def authenticate():
        raise Auth.exceptions.HTTPException(status_code=status, detail="Authentication unavailable")

    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/threads")
    assert response.status_code == status


@pytest.mark.asyncio
async def test_authentication_exception_does_not_leak_internal_error(monkeypatch):
    import httpx

    from langhost import server

    auth = Auth()

    @auth.authenticate
    async def authenticate():
        raise RuntimeError("private infrastructure detail")

    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/threads")
    assert response.status_code == 500
    assert "private infrastructure detail" not in response.text


@pytest.mark.asyncio
async def test_denied_assistant_does_not_create_implicit_thread(monkeypatch):
    import os
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from langgraph_runtime_pg.models import ThreadRow
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

    @auth.authenticate
    async def authenticate():
        return {"identity": "alice"}

    @auth.on.assistants.read
    async def deny_assistant(ctx, value):
        return False

    monkeypatch.setattr(core_api, "connect", connect)
    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    thread_id = uuid4()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/threads/{thread_id}/runs",
                json={"assistant_id": str(uuid4()), "if_not_exists": "create"},
            )
        assert response.status_code == 403
        async with factory() as session:
            assert await session.get(ThreadRow, thread_id) is None
    finally:
        await engine.dispose()


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
async def test_store_namespace_auth_isolates_real_postgres_items(monkeypatch):
    import asyncio
    import os
    from uuid import uuid4

    import httpx
    from sqlalchemy.engine import make_url

    from langgraph_runtime_pg.database import start_pool, stop_pool
    from langgraph_runtime_pg.store import Store
    from langhost import server

    uri = os.environ.get("BOUNDARY_TEST_DATABASE_URI")
    if not uri:
        pytest.skip("Set BOUNDARY_TEST_DATABASE_URI to an isolated PostgreSQL database")
    if not (make_url(uri).database or "").startswith("graphharbor_boundary_"):
        pytest.fail("Store integration test requires a graphharbor_boundary_ database")
    monkeypatch.setenv("DATABASE_URI", uri)
    monkeypatch.setenv("GRAPHHARBOR_REDIS_PREFIX", f"graphharbor:store-test:{uuid4().hex}")
    await stop_pool()
    await start_pool()
    auth = Auth()

    @auth.authenticate
    async def authenticate(authorization):
        return {"identity": authorization}

    @auth.on.store
    async def scope(ctx, value):
        value["namespace"] = ("users", ctx.user.identity, *(value.get("namespace") or ()))

    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    identity = f"alice-{uuid4().hex}"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            alice = {"Authorization": identity}
            bob = {"Authorization": "bob"}
            item = {"namespace": ["notes"], "key": "one", "value": {"text": "private"}}
            assert (await client.put("/store/items", json=item, headers=alice)).status_code == 204
            assert (await client.get("/store/items?namespace=notes&key=one", headers=bob)).json() is None
            found = await client.get("/store/items?namespace=notes&key=one", headers=alice)
            assert found.json()["value"] == item["value"]
            search = await client.post(
                "/store/items/search", json={"namespace_prefix": ["notes"]}, headers=alice
            )
            assert len(search.json()["items"]) == 1
            assert (await client.post(
                "/store/items/search", json={"namespace_prefix": ["notes"]}, headers=bob
            )).json()["items"] == []
            namespaces = await client.post(
                "/store/namespaces", json={"suffix": ["notes"], "max_depth": 3}, headers=alice
            )
            assert namespaces.json()["namespaces"] == [["users", identity, "notes"]]
            assert (await client.post("/store/namespaces", json={}, headers=bob)).json() == {
                "namespaces": []
            }
            expiring = {**item, "key": "expiring", "ttl": 0.001, "index": False}
            assert (await client.put("/store/items", json=expiring, headers=alice)).status_code == 204
            await asyncio.sleep(0.2)
            assert await Store().sweep_ttl() >= 1
            assert (await client.get(
                "/store/items?namespace=notes&key=expiring", headers=alice
            )).json() is None
            assert (
                await client.request("DELETE", "/store/items", json=item, headers=alice)
            ).status_code == 204
            assert (await client.get("/store/items?namespace=notes&key=one", headers=alice)).json() is None
    finally:
        await stop_pool()


@pytest.mark.asyncio
async def test_protocol_resume_lookup_uses_authenticated_idempotency_key(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from langgraph_runtime_pg.auth import Principal
    from langhost import protocol_api

    principal = Principal.from_auth_user({"identity": "alice"})
    thread_id = uuid4()
    statements = []

    class Session:
        async def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    @asynccontextmanager
    async def connect():
        yield SimpleNamespace(session=Session())

    monkeypatch.setattr(protocol_api, "connect", connect)
    request = SimpleNamespace(
        path_params={"thread_id": str(thread_id)}, scope={"principal": principal}
    )
    assert await protocol_api._run_by_idempotency("retry", request) is None
    assert principal.idempotency_key("retry") in statements[0].compile().params.values()


@pytest.mark.asyncio
async def test_denied_cron_store_and_protocol_run_have_no_side_effects(monkeypatch):
    from uuid import uuid4

    import httpx

    from langhost import core_api, server, store_api

    auth = Auth()

    @auth.authenticate
    async def authenticate():
        return {"identity": "alice"}

    @auth.on.crons.create
    async def deny_cron(ctx, value):
        return False

    @auth.on.store.put
    async def deny_store(ctx, value):
        return False

    @auth.on.threads.create_run
    async def deny_run(ctx, value):
        return False

    def forbidden_dependency(*args, **kwargs):
        raise AssertionError("denied request reached persistence")

    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    monkeypatch.setattr(core_api, "connect", forbidden_dependency)
    monkeypatch.setattr(store_api, "_store", forbidden_dependency)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    thread_id = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        cron = await client.post(
            "/runs/crons", json={"assistant_id": str(uuid4()), "schedule": "* * * * *"}
        )
        store = await client.put(
            "/store/items", json={"namespace": ["notes"], "key": "one", "value": {}}
        )
        protocol = await client.post(
            f"/threads/{thread_id}/commands",
            json={"id": 1, "method": "run.start", "params": {"assistant_id": str(uuid4())}},
        )
    assert cron.status_code == store.status_code == protocol.status_code == 403


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
            assert (await client.delete(path, headers=bob)).status_code == 404
            assert (await client.get(path, headers=alice)).status_code == 200
            denied_stream = await client.get(f"{path}/stream", headers=bob)
            assert denied_stream.status_code == 200
            assert denied_stream.headers["content-type"].startswith("text/event-stream")
            assert 'event: error\ndata: {"error":"HTTPException","message":"404: Thread not found"}' in denied_stream.text
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
async def test_auth_dispatch_matches_locked_official_reference(monkeypatch):
    monkeypatch.setenv("REDIS_URI", "redis://localhost:6379/0")
    monkeypatch.setenv("DATABASE_URI", "postgresql://localhost/graphharbor_official_auth_probe")
    from langgraph_api.auth import custom

    from langgraph_runtime_pg.authorization import AuthUser

    auth = Auth()
    calls = []

    @auth.on
    async def global_handler(ctx, value):
        calls.append("global")
        return False

    @auth.on.threads
    async def resource_handler(ctx, value):
        calls.append("resource")
        value["metadata"] = {"owner": ctx.user.identity}
        return {"owner": ctx.user.identity}

    @auth.on.threads.create
    async def create_handler(ctx, value):
        calls.append("create")
        value["metadata"] = {"created_by": ctx.user.identity}
        return None

    monkeypatch.setattr(custom, "get_auth_instance", lambda: auth)
    for resource, action in (("threads", "create"), ("threads", "read"), ("assistants", "read")):
        official_value = {}
        local_value = {}
        ctx = Auth.types.AuthContext(
            user=AuthUser({"identity": "alice"}),
            permissions=[],
            resource=resource,
            action=action,
        )
        calls.clear()
        try:
            official_result = await custom.handle_event(ctx, official_value)
            official_status = None
        except HTTPException as exc:
            official_result, official_status = None, exc.status_code
        official_calls = calls[:]
        calls.clear()
        try:
            local_result = await authorize(auth, {"identity": "alice"}, resource, action, local_value)
            local_status = None
        except HTTPException as exc:
            local_result, local_status = None, exc.status_code
        assert (local_status, local_result or {}, local_value, calls) == (
            official_status, official_result or {}, official_value, official_calls
        )


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
