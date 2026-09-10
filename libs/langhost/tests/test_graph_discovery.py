from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from starlette.testclient import TestClient

from langgraph_runtime_pg.auth import PrincipalMiddleware
from langgraph_runtime_pg.graph_registry import GraphRegistry
from langhost import core_api


@pytest.fixture
def discovery_app(tmp_path, monkeypatch):
    (tmp_path / "graph.py").write_text(
        '''from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
class Input(TypedDict):
    question: str
class Output(TypedDict):
    answer: str
class State(Input, Output):
    internal_count: int
class Context(TypedDict):
    language: str
def respond(state):
    raise AssertionError("schema discovery must not execute nodes")
graph = (StateGraph(State, input_schema=Input, output_schema=Output, context_schema=Context)
    .add_node("respond", respond).add_edge(START, "respond").add_edge("respond", END).compile())
'''
    )
    registry = GraphRegistry({"agent": "graph.py:graph"}, base_dir=tmp_path)
    monkeypatch.setattr(core_api, "connect", Mock(side_effect=AssertionError("unexpected database access")))
    app = Starlette(routes=[
        Route("/assistants/search", core_api.assistants_search, methods=["POST"]),
        Route("/assistants/count", core_api.assistants_count, methods=["POST"]),
        Route("/assistants/{assistant_id}/schemas", core_api.assistants_schemas),
    ])
    app.state.graph_registry = registry
    return app


def test_registry_and_schemas_without_assistant_records(discovery_app):
    graph = discovery_app.state.graph_registry.get("agent")
    original_output = list(graph.output_channels)
    with TestClient(discovery_app) as client:
        assert client.get("/graphs").status_code == 404
        result = client.get("/assistants/agent/schemas")
        assert result.status_code == 200
        schemas = result.json()
        assert set(schemas["input_schema"]["properties"]) == {"question"}
        assert set(schemas["output_schema"]["properties"]) == {"answer"}
        assert set(schemas["state_schema"]["properties"]) == {"question", "answer", "internal_count"}
        assert set(schemas["context_schema"]["properties"]) == {"language"}
        assert client.get("/assistants/missing/schemas").status_code == 404
    assert graph.output_channels == original_output


def test_discovery_requires_authentication(discovery_app):
    discovery_app.add_middleware(PrincipalMiddleware, allow_anonymous=False)
    with TestClient(discovery_app) as client:
        assert client.post("/assistants/search", json={}).status_code == 401
        assert client.get("/assistants/agent/schemas").status_code == 401


def test_assistant_uuid_schema_respects_project_scope(discovery_app, monkeypatch):
    assistant_id = uuid4()
    row = SimpleNamespace(graph_id="agent", tenant_id="tenant", project_id="project-a")

    async def get(model, key):
        assert key == assistant_id
        return row

    @asynccontextmanager
    async def connect():
        yield SimpleNamespace(session=SimpleNamespace(get=get))

    monkeypatch.setattr(core_api, "connect", connect)
    principal = SimpleNamespace(scope_filter=lambda: {"tenant_id": "tenant", "project_id": "project-a"})
    monkeypatch.setattr(core_api, "_principal", lambda request: principal)
    with TestClient(discovery_app) as client:
        assert client.get(f"/assistants/{assistant_id}/schemas").status_code == 200
        principal.scope_filter = lambda: {"tenant_id": "tenant", "project_id": "project-b"}
        assert client.get(f"/assistants/{assistant_id}/schemas").status_code == 404


@pytest.mark.asyncio
async def test_defaults_registered_with_official_identity_and_searchable(discovery_app, monkeypatch):
    from datetime import UTC, datetime

    session = SimpleNamespace(execute=AsyncMock(), scalar=AsyncMock(return_value=1))

    @asynccontextmanager
    async def connect():
        yield SimpleNamespace(session=session)

    monkeypatch.setattr(core_api, "connect", connect)
    registry = discovery_app.state.graph_registry
    await core_api.register_default_assistants(registry)
    writes = [call.args[0] for call in session.execute.await_args_list]
    assert len(writes) == 2
    for statement in writes:
        assert statement.compile().params["assistant_id"] == uuid5(NAMESPACE_URL, "agent")
        assert statement.compile().params["metadata"] == {"created_by": "system"}
        assert "ON CONFLICT" in str(statement)
        assert "DO NOTHING" in str(statement)
    row = SimpleNamespace(
        assistant_id=uuid5(NAMESPACE_URL, "agent"), graph_id="agent", name="agent",
        tenant_id=None, project_id=None, metadata_={"created_by": "system"},
        config={}, context={}, version=1, description=None,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    session.execute.reset_mock()
    session.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row]))
    principal = SimpleNamespace(tenant_id="tenant", project_id="project", scope_filter=lambda: {
        "tenant_id": "tenant", "project_id": "project",
    })
    monkeypatch.setattr(core_api, "_principal", lambda request: principal)
    assert core_api._assistant_readable(row, principal)
    with TestClient(discovery_app) as client:
        found = client.post("/assistants/search", json={"metadata": {"created_by": "system"}, "limit": 1})
        assert found.status_code == 200
        assert found.json()[0]["assistant_id"] == str(uuid5(NAMESPACE_URL, "agent"))
        assert client.post("/assistants/count", json={}).json() == 1
        assert client.post("/assistants/search", json={"limit": 0}).status_code == 422
    query = session.execute.await_args_list[0].args[0]
    sql = str(query)
    assert "tenant_id IS NULL" in sql and "project_id IS NULL" in sql
    assert "LIMIT" in sql and "OFFSET" in sql
    session.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: row)
    resolved = await core_api._resolve_assistant(
        Request({"type": "http", "app": discovery_app}), session, "agent", principal,
    )
    assert resolved is row
    query = session.execute.await_args_list[-1].args[0]
    assert uuid5(NAMESPACE_URL, "agent") in query.compile().params.values()
