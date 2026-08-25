"""GraphHarbor-owned ASGI boundary for the production profile."""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import uvicorn
from langgraph_cli.config import validate_config_file
from sqlalchemy import func, select
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from langgraph_runtime_pg.auth import (
    DelegationJWTValidator,
    PrincipalMiddleware,
    in_principal_scope,
    principal_from_scope,
    scope_override_error,
)
from langgraph_runtime_pg.checkpoint import delete_thread_checkpoints, get_checkpointer
from langgraph_runtime_pg.database import connect, pool_stats
from langgraph_runtime_pg.graph_registry import GraphRegistry
from langgraph_runtime_pg.metrics import prometheus_text, set_gauge
from langgraph_runtime_pg.models import (
    AssistantRow,
    AssistantVersionRow,
    RunRow,
    ThreadRow,
)
from langgraph_runtime_pg.production import RuntimeReadiness, lifespan as runtime_lifespan
from langgraph_runtime_pg.protocol import RunReason, RunStatus, official_info_document
from langgraph_runtime_pg.redis_stream import wake_run_queue
from langgraph_runtime_pg.run_state import is_terminal, transition
from langgraph_runtime_pg.run_store import RunRepository
from langhost.core_api import (
    assistants_count,
    assistants_create,
    assistants_delete,
    assistants_get,
    assistants_graph,
    assistants_latest,
    assistants_schemas,
    assistants_search,
    assistants_subgraphs,
    assistants_update,
    assistants_versions,
    cron_create_root,
    cron_create_thread,
    cron_delete,
    cron_update,
    crons_count,
    crons_search,
    runs_batch,
    runs_cancel,
    runs_cancel_many,
    runs_create_root,
    runs_create_thread,
    runs_delete,
    runs_get,
    runs_join,
    runs_list,
    runs_wait,
    runs_wait_root,
    threads_copy,
    threads_count,
    threads_create,
    threads_delete,
    threads_get,
    threads_history,
    threads_prune,
    threads_search,
    threads_state,
    threads_update,
    threads_update_state,
)
from langhost.protocol_api import protocol_commands, protocol_event_stream
from langhost.streaming import runs_stream, runs_stream_existing


def _plain(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _config_value(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _load_symbol(spec: str, base_dir: pathlib.Path) -> Any:
    path_text, separator, symbol = spec.partition(":")
    if not separator or not symbol:
        raise ValueError(f"invalid application path {spec!r}; expected path.py:symbol")
    path = (base_dir / path_text).resolve()
    module_name = f"graphharbor_custom_{path.stem}_{abs(hash(path))}"
    module_spec = importlib.util.spec_from_file_location(module_name, path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot load custom app {path}")
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return getattr(module, symbol)


def _validate_graph_specs(config: Any, base_dir: pathlib.Path) -> None:
    graphs = _config_value(config, "graphs", {}) or {}
    for graph_id, spec in graphs.items():
        graph_path = spec if isinstance(spec, str) else _config_value(spec, "path")
        if not graph_path:
            raise ValueError(f"graph {graph_id!r} has no path")
        path_text = str(graph_path).partition(":")[0]
        path = (base_dir / path_text).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"graph {graph_id!r} does not exist: {path}")


async def _ok(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def _live(_: Request) -> JSONResponse:
    return JSONResponse({"live": True})


async def _ready(request: Request) -> JSONResponse:
    readiness = getattr(request.app.state, "readiness", None)
    if readiness is None or not readiness.ready:
        return JSONResponse(
            {
                "ready": False,
                "reason": getattr(readiness, "reason", "not started"),
                "checks": getattr(readiness, "checks", None),
            },
            status_code=503,
        )
    return JSONResponse({"ready": True, "checks": getattr(readiness, "checks", None)})


def _principal(request: Request):
    return principal_from_scope(request.scope)


async def _info(request: Request) -> JSONResponse:
    del request
    return JSONResponse(official_info_document())


async def _openapi(request: Request) -> JSONResponse:
    del request
    return JSONResponse(
        {
            "openapi": "3.1.0",
            "info": {"title": "GraphHarbor Agent Server", "version": "1"},
            "paths": {
                "/ok": {"get": {"responses": {"200": {"description": "ready"}}}},
                "/live": {"get": {"responses": {"200": {"description": "alive"}}}},
                "/ready": {"get": {"responses": {"200": {"description": "ready"}}}},
                "/info": {"get": {"responses": {"200": {"description": "capabilities"}}}},
                "/metrics": {"get": {"responses": {"200": {"description": "Prometheus metrics"}}}},
                "/assistants": {"get": {}, "post": {}},
                "/assistants/search": {"post": {}},
                "/assistants/count": {"post": {}},
                "/assistants/{assistant_id}": {"get": {}, "patch": {}, "delete": {}},
                "/assistants/{assistant_id}/versions": {"post": {}},
                "/assistants/{assistant_id}/latest": {"post": {}},
                "/assistants/{assistant_id}/graph": {"get": {}},
                "/assistants/{assistant_id}/schemas": {"get": {}},
                "/assistants/{assistant_id}/subgraphs": {"get": {}},
                "/assistants/{assistant_id}/subgraphs/{namespace}": {"get": {}},
                "/threads": {"get": {}, "post": {}},
                "/threads/search": {"post": {}},
                "/threads/count": {"post": {}},
                "/threads/prune": {"post": {}},
                "/threads/{thread_id}": {"get": {}, "patch": {}, "delete": {}},
                "/threads/{thread_id}/copy": {"post": {}},
                "/threads/{thread_id}/state": {"get": {}, "post": {}, "patch": {}},
                "/threads/{thread_id}/state/checkpoint": {"post": {}},
                "/threads/{thread_id}/state/{checkpoint_id}": {"get": {}},
                "/threads/{thread_id}/history": {"post": {}},
                "/runs": {"post": {}},
                "/runs/wait": {"post": {}},
                "/runs/batch": {"post": {}},
                "/runs/cancel": {"post": {}},
                "/runs/stream": {"post": {}},
                "/runs/{run_id}/stream": {"get": {}},
                "/threads/{thread_id}/runs": {"get": {}, "post": {}},
                "/threads/{thread_id}/runs/wait": {"post": {}},
                "/threads/{thread_id}/runs/stream": {"post": {}},
                "/threads/{thread_id}/runs/{run_id}/stream": {"get": {}},
                "/threads/{thread_id}/runs/{run_id}": {"get": {}, "delete": {}},
                "/threads/{thread_id}/runs/{run_id}/cancel": {"post": {}},
                "/threads/{thread_id}/runs/{run_id}/join": {"get": {}},
                "/threads/{thread_id}/commands": {"post": {}},
                "/threads/{thread_id}/stream/events": {"post": {}},
                "/runs/crons": {"post": {}},
                "/runs/crons/search": {"post": {}},
                "/runs/crons/count": {"post": {}},
                "/runs/crons/{cron_id}": {"patch": {}, "delete": {}},
                "/threads/{thread_id}/runs/crons": {"post": {}},
            },
        }
    )


async def _metrics(_: Request):
    from starlette.responses import PlainTextResponse

    for name, value in pool_stats().items():
        set_gauge(f"graphharbor_postgres_pool_{name}", value)
    try:
        from langgraph_runtime_pg.redis_stream import transport_stats

        for name, value in (await transport_stats()).items():
            set_gauge(f"graphharbor_redis_{name}", value)
    except Exception:
        set_gauge("graphharbor_redis_connected", 0)
    return PlainTextResponse(prometheus_text(), media_type="text/plain; version=0.0.4")


def _no_content() -> Response:
    return Response(status_code=204)


async def _capability_unavailable(request: Request) -> JSONResponse:
    capability = request.path_params.get("capability", "stream_v2")
    return JSONResponse(
        {
            "detail": f"capability {capability!r} is not enabled in the foundation profile",
            "capability": capability,
            "status": 501,
        },
        status_code=501,
    )


def _scope_query(query: Any, model: Any, principal: Any) -> Any:
    if principal is not None:
        query = query.where(
            model.tenant_id == principal.tenant_id,
            model.project_id == principal.project_id,
        )
    return query


def _metadata_query(query: Any, model: Any, metadata: Any) -> Any:
    if isinstance(metadata, dict) and metadata:
        query = query.where(model.metadata_.contains(metadata))
    return query


def _request_limit_offset(request: Request) -> tuple[int, int]:
    try:
        limit = max(1, min(int(request.query_params.get("limit", "10")), 1000))
        offset = max(0, int(request.query_params.get("offset", "0")))
    except ValueError as exc:
        raise ValueError("limit and offset must be integers") from exc
    return limit, offset


async def _assistant_search(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    try:
        limit, offset = _request_limit_offset(request)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    query = select(AssistantRow).order_by(AssistantRow.created_at.desc())
    query = _scope_query(query, AssistantRow, principal)
    query = _metadata_query(query, AssistantRow, payload.get("metadata"))
    if payload.get("graph_id"):
        query = query.where(AssistantRow.graph_id == str(payload["graph_id"]))
    if payload.get("name"):
        query = query.where(AssistantRow.name.ilike(f"%{payload['name']}%"))
    async with connect() as conn:
        rows = (await conn.session.execute(query.limit(limit).offset(offset))).scalars().all()
        values = [_assistant_payload(row) for row in rows]
    if payload.get("response_format") == "object":
        return JSONResponse({"assistants": values, "next": None})
    return JSONResponse(values)


async def _assistant_count(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    query = select(func.count()).select_from(AssistantRow)
    query = _scope_query(query, AssistantRow, principal)
    query = _metadata_query(query, AssistantRow, payload.get("metadata"))
    if payload.get("graph_id"):
        query = query.where(AssistantRow.graph_id == str(payload["graph_id"]))
    if payload.get("name"):
        query = query.where(AssistantRow.name.ilike(f"%{payload['name']}%"))
    async with connect() as conn:
        count = int(await conn.session.scalar(query) or 0)
    return JSONResponse(count)


async def _assistant_update(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        assistant_id = UUID(request.path_params["assistant_id"])
    except ValueError:
        return JSONResponse({"detail": "assistant not found"}, status_code=404)
    payload = await request.json()
    if error := scope_override_error(payload, principal):
        return JSONResponse({"detail": error}, status_code=403)
    async with connect() as conn:
        query = _scope_query(
            select(AssistantRow).where(AssistantRow.assistant_id == assistant_id),
            AssistantRow,
            principal,
        )
        row = (await conn.session.execute(query)).scalar_one_or_none()
        if row is None:
            return JSONResponse({"detail": "assistant not found"}, status_code=404)
        for field in ("graph_id", "name", "description", "config", "context"):
            if field in payload:
                setattr(row, field, payload[field])
        if isinstance(payload.get("metadata"), dict):
            row.metadata_ = {**row.metadata_, **payload["metadata"]}
        row.version += 1
        row.updated_at = datetime.now(UTC)
        conn.session.add(
            AssistantVersionRow(
                assistant_id=row.assistant_id,
                version=row.version,
                graph_id=row.graph_id,
                config=row.config,
                context=row.context,
                metadata_=row.metadata_,
                name=row.name,
                description=row.description,
            )
        )
        await conn.session.flush()
        return JSONResponse(_assistant_payload(row))


async def _assistant_delete(request: Request) -> JSONResponse | Response:
    principal = _principal(request)
    try:
        assistant_id = UUID(request.path_params["assistant_id"])
    except ValueError:
        return _no_content()
    async with connect() as conn:
        query = _scope_query(
            select(AssistantRow).where(AssistantRow.assistant_id == assistant_id),
            AssistantRow,
            principal,
        )
        row = (await conn.session.execute(query)).scalar_one_or_none()
        if row is None:
            return _no_content()
        await conn.session.delete(row)
        await conn.session.flush()
    return _no_content()


async def _assistants(request: Request) -> JSONResponse:
    principal = _principal(request)
    async with connect() as conn:
        if request.method == "POST":
            payload = await request.json()
            assistant_id = (
                UUID(str(payload.get("assistant_id")))
                if payload.get("assistant_id")
                else UUID(int=0)
            )
            if assistant_id.int == 0:
                from uuid import uuid4

                assistant_id = uuid4()
            if error := scope_override_error(payload, principal):
                return JSONResponse({"detail": error}, status_code=403)
            graph_id = str(payload.get("graph_id") or "")
            registry = getattr(request.app.state, "graph_registry", None)
            if registry is not None:
                try:
                    registry.get(graph_id)
                except KeyError:
                    return JSONResponse({"detail": "graph not found"}, status_code=404)
            row = AssistantRow(
                assistant_id=assistant_id,
                tenant_id=principal.tenant_id if principal else payload.get("tenant_id"),
                project_id=principal.project_id if principal else payload.get("project_id"),
                graph_id=graph_id,
                name=str(payload.get("name") or "Untitled"),
                description=payload.get("description"),
                config=payload.get("config") or {},
                context=payload.get("context") or {},
                metadata_=payload.get("metadata") or {},
                version=int(payload.get("version", 1)),
            )
            conn.session.add(row)
            conn.session.add(
                AssistantVersionRow(
                    assistant_id=assistant_id,
                    version=row.version,
                    graph_id=row.graph_id,
                    config=row.config,
                    context=row.context,
                    metadata_=row.metadata_,
                    name=row.name,
                    description=row.description,
                )
            )
            await conn.session.flush()
            return JSONResponse(_assistant_payload(row), status_code=201)

        query = _scope_query(
            select(AssistantRow).order_by(AssistantRow.created_at.desc()), AssistantRow, principal
        )
        rows = (await conn.session.execute(query)).scalars().all()
        return JSONResponse([_assistant_payload(row) for row in rows])


async def _threads(request: Request) -> JSONResponse:
    principal = _principal(request)
    async with connect() as conn:
        if request.method == "POST":
            payload = await request.json()
            from uuid import uuid4

            thread_id = UUID(str(payload["thread_id"])) if payload.get("thread_id") else uuid4()
            if error := scope_override_error(payload, principal):
                return JSONResponse({"detail": error}, status_code=403)
            row = ThreadRow(
                thread_id=thread_id,
                tenant_id=principal.tenant_id if principal else payload.get("tenant_id"),
                project_id=principal.project_id if principal else payload.get("project_id"),
                graph_id=payload.get("graph_id") or (payload.get("metadata") or {}).get("graph_id"),
                status="idle",
                metadata_=payload.get("metadata") or {},
                config=payload.get("config") or {},
                interrupts={},
            )
            conn.session.add(row)
            await conn.session.flush()
            return JSONResponse(_thread_payload(row), status_code=201)

        query = select(ThreadRow).order_by(ThreadRow.created_at.desc())
        if principal:
            query = query.where(
                ThreadRow.tenant_id == principal.tenant_id,
                ThreadRow.project_id == principal.project_id,
            )
        rows = (await conn.session.execute(query)).scalars().all()
        return JSONResponse([_thread_payload(row) for row in rows])


async def _assistant_get(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        assistant_id = UUID(request.path_params["assistant_id"])
    except ValueError:
        return JSONResponse({"detail": "assistant not found"}, status_code=404)
    async with connect() as conn:
        query = select(AssistantRow).where(AssistantRow.assistant_id == assistant_id)
        if principal:
            query = query.where(
                AssistantRow.tenant_id == principal.tenant_id,
                AssistantRow.project_id == principal.project_id,
            )
        row = (await conn.session.execute(query)).scalar_one_or_none()
        if row is None:
            return JSONResponse({"detail": "assistant not found"}, status_code=404)
        return JSONResponse(_assistant_payload(row))


async def _thread_get(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        thread_id = UUID(request.path_params["thread_id"])
    except ValueError:
        return JSONResponse({"detail": "thread not found"}, status_code=404)
    async with connect() as conn:
        row = await conn.session.get(ThreadRow, thread_id)
        if row is None or not in_principal_scope(row, principal):
            return JSONResponse({"detail": "thread not found"}, status_code=404)
        return JSONResponse(_thread_payload(row))


async def _resolve_assistant(
    session: Any, assistant_value: str, principal: Any
) -> AssistantRow | None:
    try:
        assistant_id = UUID(assistant_value)
        query = select(AssistantRow).where(AssistantRow.assistant_id == assistant_id)
    except ValueError:
        query = select(AssistantRow).where(AssistantRow.graph_id == assistant_value)
    if principal:
        query = query.where(
            AssistantRow.tenant_id == principal.tenant_id,
            AssistantRow.project_id == principal.project_id,
        )
    return (await session.execute(query.limit(1))).scalar_one_or_none()


async def _run_create(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    assistant_value = str(payload.get("assistant_id", ""))
    thread_value = request.path_params.get("thread_id")
    if not assistant_value:
        return JSONResponse({"detail": "assistant_id is required"}, status_code=422)
    async with connect() as conn:
        assistant = await _resolve_assistant(conn.session, assistant_value, principal)
        if assistant is None:
            return JSONResponse({"detail": "assistant not found"}, status_code=404)
        thread = None
        thread_id = UUID(str(thread_value)) if thread_value else None
        if thread_id is not None:
            thread = await conn.session.get(ThreadRow, thread_id)
            if thread is None or not in_principal_scope(thread, principal):
                return JSONResponse({"detail": "thread not found"}, status_code=404)
        idempotency_key = request.headers.get("idempotency-key") or payload.get("idempotency_key")
        if error := scope_override_error(payload, principal):
            return JSONResponse({"detail": error}, status_code=403)
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant.assistant_id,
            thread_id=thread_id,
            kwargs=payload,
            metadata=payload.get("metadata") or {},
            tenant_id=principal.tenant_id if principal else getattr(thread, "tenant_id", None),
            project_id=principal.project_id if principal else getattr(thread, "project_id", None),
            idempotency_key=idempotency_key,
        )
        await conn.session.refresh(run)
        conn.schedule_after_commit(wake_run_queue)
        return JSONResponse(_run_payload(run), status_code=201)


async def _run_get(request: Request) -> JSONResponse:
    principal = _principal(request)
    run_id = UUID(request.path_params["run_id"])
    thread_id = UUID(request.path_params["thread_id"])
    async with connect() as conn:
        run = await conn.session.get(RunRow, run_id)
        if run is None or run.thread_id != thread_id or not in_principal_scope(run, principal):
            return JSONResponse({"detail": "run not found"}, status_code=404)
        return JSONResponse(_run_payload(run))


async def _run_list(request: Request) -> JSONResponse:
    principal = _principal(request)
    thread_id = UUID(request.path_params["thread_id"])
    async with connect() as conn:
        query = (
            select(RunRow).where(RunRow.thread_id == thread_id).order_by(RunRow.created_at.desc())
        )
        if principal:
            query = query.where(
                RunRow.tenant_id == principal.tenant_id,
                RunRow.project_id == principal.project_id,
            )
        rows = (await conn.session.execute(query)).scalars().all()
        return JSONResponse([_run_payload(row) for row in rows])


async def _run_cancel(request: Request) -> JSONResponse:
    principal = _principal(request)
    run_id = UUID(request.path_params["run_id"])
    thread_id = UUID(request.path_params["thread_id"])
    action = request.query_params.get("action", "interrupt")
    if action not in {"interrupt", "rollback"}:
        return JSONResponse({"detail": "action must be interrupt or rollback"}, status_code=422)
    async with connect() as conn:
        run = await conn.session.get(RunRow, run_id)
        if (
            run is None
            or run.thread_id != thread_id
            or (
                principal
                and (run.tenant_id != principal.tenant_id or run.project_id != principal.project_id)
            )
        ):
            return JSONResponse({"detail": "run not found"}, status_code=404)
        if action == "rollback":
            await conn.session.delete(run)
            await conn.session.flush()
            conn.schedule_after_commit(lambda: delete_thread_checkpoints(str(thread_id)))
            return JSONResponse({}, status_code=200)
        if is_terminal(run.status):
            return JSONResponse(_run_payload(run), status_code=200)
        change = transition(
            run.status,
            RunStatus.INTERRUPTED,
            reason=RunReason.CANCEL_REQUESTED,
            retry_count=run.retry_count,
        )
        run.status = change.status.value
        run.reason = change.reason.value
        run.updated_at = datetime.now(UTC)
        await conn.session.flush()
        from langgraph_runtime_pg.redis_stream import Message, get_stream_manager

        async def _publish_cancel() -> None:
            try:
                await get_stream_manager().put(
                    run_id,
                    thread_id,
                    Message(topic=b"run:control", data=json.dumps({"action": action}).encode()),
                )
            except RuntimeError:
                pass

        conn.schedule_after_commit(_publish_cancel)
        return JSONResponse(_run_payload(run), status_code=200)


def _assistant_payload(row: AssistantRow) -> dict[str, Any]:
    return _plain(
        {
            "assistant_id": row.assistant_id,
            "graph_id": row.graph_id,
            "name": row.name,
            "description": row.description,
            "config": row.config,
            "context": row.context,
            "metadata": row.metadata_,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def _thread_payload(row: ThreadRow) -> dict[str, Any]:
    return _plain(
        {
            "thread_id": row.thread_id,
            "status": row.status,
            "metadata": row.metadata_,
            "config": row.config,
            "values": row.values_,
            "interrupts": row.interrupts,
            "error": row.error,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "state_updated_at": row.state_updated_at,
        }
    )


def _run_payload(row: RunRow) -> dict[str, Any]:
    return _plain(
        {
            "run_id": row.run_id,
            "thread_id": row.thread_id,
            "assistant_id": row.assistant_id,
            "status": row.status,
            "metadata": row.metadata_,
            "kwargs": row.kwargs,
            "multitask_strategy": row.multitask_strategy,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def create_app(
    config: dict[str, Any] | Any,
    *,
    base_dir: pathlib.Path | None = None,
    custom_app: Any | None = None,
) -> Starlette:
    base_dir = base_dir or pathlib.Path.cwd()
    readiness = RuntimeReadiness()
    http_config = _config_value(config, "http", {}) or {}
    app_spec = _config_value(http_config, "app") if isinstance(http_config, dict) else None
    custom_app = custom_app or (_load_symbol(app_spec, base_dir) if app_spec else None)
    auth_config = _config_value(config, "auth", {}) or {}
    auth_spec = auth_config if isinstance(auth_config, str) else _config_value(auth_config, "path")
    auth_handler = _load_symbol(auth_spec, base_dir) if auth_spec else None

    @asynccontextmanager
    async def lifespan(app: Starlette):
        _validate_graph_specs(config, base_dir)
        app.state.graph_registry = GraphRegistry.from_config(config, base_dir=base_dir)
        readiness.checks = {"graphs": len(app.state.graph_registry) > 0}
        async with runtime_lifespan(app, readiness=readiness):
            if len(app.state.graph_registry) > 0:
                app.state.graph_registry.attach_checkpointer(get_checkpointer())
            if custom_app is not None and hasattr(
                getattr(custom_app, "router", None), "lifespan_context"
            ):
                async with custom_app.router.lifespan_context(custom_app):
                    yield
            else:
                yield

    routes: list[Any] = [
        Route("/ok", _ok, methods=["GET"]),
        Route("/live", _live, methods=["GET"]),
        Route("/ready", _ready, methods=["GET"]),
        Route("/info", _info, methods=["GET"]),
        Route("/openapi.json", _openapi, methods=["GET"]),
        Route("/metrics", _metrics, methods=["GET"]),
        Route("/assistants/search", assistants_search, methods=["POST"]),
        Route("/assistants/count", assistants_count, methods=["POST"]),
        Route("/assistants", _assistants, methods=["GET"]),
        Route("/assistants", assistants_create, methods=["POST"]),
        Route("/assistants/{assistant_id}/versions", assistants_versions, methods=["POST"]),
        Route("/assistants/{assistant_id}/latest", assistants_latest, methods=["POST"]),
        Route("/assistants/{assistant_id}/graph", assistants_graph, methods=["GET"]),
        Route("/assistants/{assistant_id}/schemas", assistants_schemas, methods=["GET"]),
        Route(
            "/assistants/{assistant_id}/subgraphs/{namespace}",
            assistants_subgraphs,
            methods=["GET"],
        ),
        Route("/assistants/{assistant_id}/subgraphs", assistants_subgraphs, methods=["GET"]),
        Route("/assistants/{assistant_id}", assistants_get, methods=["GET"]),
        Route("/assistants/{assistant_id}", assistants_update, methods=["PATCH"]),
        Route("/assistants/{assistant_id}", assistants_delete, methods=["DELETE"]),
        Route("/threads/search", threads_search, methods=["POST"]),
        Route("/threads/count", threads_count, methods=["POST"]),
        Route("/threads/prune", threads_prune, methods=["POST"]),
        Route("/threads", _threads, methods=["GET"]),
        Route("/threads", threads_create, methods=["POST"]),
        Route("/threads/{thread_id}/copy", threads_copy, methods=["POST"]),
        Route("/threads/{thread_id}/state/checkpoint", threads_state, methods=["POST"]),
        Route("/threads/{thread_id}/state/{checkpoint_id}", threads_state, methods=["GET"]),
        Route("/threads/{thread_id}/state", threads_state, methods=["GET"]),
        Route("/threads/{thread_id}/state", threads_update_state, methods=["POST", "PATCH"]),
        Route("/threads/{thread_id}/history", threads_history, methods=["POST"]),
        Route("/threads/{thread_id}", threads_get, methods=["GET"]),
        Route("/threads/{thread_id}", threads_update, methods=["PATCH"]),
        Route("/threads/{thread_id}", threads_delete, methods=["DELETE"]),
        Route("/runs/{run_id}/stream", runs_stream_existing, methods=["GET"]),
        Route("/runs/stream", runs_stream, methods=["POST"]),
        Route("/runs/batch", runs_batch, methods=["POST"]),
        Route("/runs/cancel", runs_cancel_many, methods=["POST"]),
        Route("/runs/wait", runs_wait_root, methods=["POST"]),
        Route("/runs", runs_create_root, methods=["POST"]),
        Route("/threads/{thread_id}/runs/crons", cron_create_thread, methods=["POST"]),
        Route("/threads/{thread_id}/runs/wait", runs_wait, methods=["POST"]),
        Route("/threads/{thread_id}/runs", runs_list, methods=["GET"]),
        Route("/threads/{thread_id}/runs/{run_id}/stream", runs_stream_existing, methods=["GET"]),
        Route("/threads/{thread_id}/runs/stream", runs_stream, methods=["POST"]),
        Route("/threads/{thread_id}/commands", protocol_commands, methods=["POST"]),
        Route("/threads/{thread_id}/stream/events", protocol_event_stream, methods=["POST"]),
        Route("/threads/{thread_id}/runs", runs_create_thread, methods=["POST"]),
        Route("/threads/{thread_id}/runs/{run_id}/cancel", runs_cancel, methods=["POST"]),
        Route("/threads/{thread_id}/runs/{run_id}/join", runs_join, methods=["GET"]),
        Route("/threads/{thread_id}/runs/{run_id}", runs_get, methods=["GET"]),
        Route("/threads/{thread_id}/runs/{run_id}", runs_delete, methods=["DELETE"]),
        Route("/runs/crons/search", crons_search, methods=["POST"]),
        Route("/runs/crons/count", crons_count, methods=["POST"]),
        Route("/runs/crons", cron_create_root, methods=["POST"]),
        Route("/runs/crons/{cron_id}", cron_update, methods=["PATCH"]),
        Route("/runs/crons/{cron_id}", cron_delete, methods=["DELETE"]),
    ]
    if custom_app is not None:
        routes.append(Mount("/", app=custom_app))
    validator = None
    if os.environ.get("GRAPHHARBOR_ENV", "development") == "production":
        validator = DelegationJWTValidator.from_env()
    cors_config = http_config.get("cors", {}) if isinstance(http_config, dict) else {}
    if not isinstance(cors_config, dict):
        cors_config = {}
    cors_origins = cors_config.get("allow_origins", [])
    cors_methods = cors_config.get("allow_methods", ["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
    cors_headers = cors_config.get(
        "allow_headers", ["Authorization", "Content-Type", "Last-Event-ID"]
    )
    cors_credentials = bool(cors_config.get("allow_credentials", False))
    if isinstance(cors_origins, str):
        cors_origins = [cors_origins]
    if isinstance(cors_methods, str):
        cors_methods = [cors_methods]
    if isinstance(cors_headers, str):
        cors_headers = [cors_headers]
    middleware = [
        Middleware(
            PrincipalMiddleware,
            validator,
            auth_handler=auth_handler,
            allow_anonymous=os.environ.get("GRAPHHARBOR_ENV", "development") != "production",
        )
    ]
    if cors_origins:
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=[str(item) for item in cors_origins],
                allow_methods=[str(item) for item in cors_methods],
                allow_headers=[str(item) for item in cors_headers],
                allow_credentials=cors_credentials,
            )
        )
    app = Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=lifespan,
    )
    app.state.readiness = readiness
    # Keep the standard langgraph.json extension points observable to custom
    # routes and integration tests while GraphHarbor owns request authentication.
    app.state.auth_handler = auth_handler
    app.state.custom_app = custom_app
    return app


def run_server(host: str, port: int, reload: bool, graphs: dict[str, Any], **kwargs: Any) -> None:
    """Compatibility-shaped entry point owned by GraphHarbor, not langgraph-api."""
    if kwargs.get("__database_uri__"):
        os.environ["DATABASE_URI"] = str(kwargs["__database_uri__"])
    if kwargs.get("__redis_uri__"):
        os.environ["REDIS_URI"] = str(kwargs["__redis_uri__"])
    config = kwargs.pop("config", None) or {"graphs": graphs}
    app = create_app(config, base_dir=kwargs.pop("base_dir", None) or pathlib.Path.cwd())
    uvicorn_kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "log_level": str(kwargs.get("server_level", "info")).lower(),
        "reload": reload,
    }
    if not reload and kwargs.get("workers", 1) > 1:
        uvicorn_kwargs["workers"] = kwargs["workers"]
    if kwargs.get("ssl_certfile"):
        uvicorn_kwargs["ssl_certfile"] = kwargs["ssl_certfile"]
        uvicorn_kwargs["ssl_keyfile"] = kwargs["ssl_keyfile"]
    uvicorn.run(app, **uvicorn_kwargs)


def load_config(path: pathlib.Path) -> Any:
    return validate_config_file(path)
