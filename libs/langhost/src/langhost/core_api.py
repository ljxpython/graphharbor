"""Core Agent Server resources backed by GraphHarbor's public runtime APIs."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langchain_core.runnables import RunnableConfig
from pydantic import PydanticUserError
from sqlalchemy import delete, func, select
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from langgraph_runtime_pg.auth import (
    RuntimeContextError,
    in_principal_scope,
    principal_from_scope,
    scope_override_error,
    sign_runtime_context,
    validate_policy_overrides,
)
from langgraph_runtime_pg.checkpoint import (
    copy_thread_checkpoints,
    delete_thread_checkpoints,
    get_checkpointer,
)
from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.metrics import inc as metric_inc
from langgraph_runtime_pg.models import (
    AssistantRow,
    AssistantVersionRow,
    CronRow,
    RunLeaseRow,
    RunRow,
    RuntimeEventRow,
    ThreadRow,
)
from langgraph_runtime_pg.protocol import RunReason, RunStatus, protocol_event
from langgraph_runtime_pg.redis_stream import (
    Message,
    enqueue_run,
    get_stream_manager,
)
from langgraph_runtime_pg.run_state import is_terminal, transition
from langgraph_runtime_pg.run_store import RunRepository


def _plain(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_plain(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _plain(model_dump(mode="json"))
        except TypeError:
            return _plain(model_dump())
    slots = getattr(value.__class__, "__slots__", ())
    if slots:
        names = (slots,) if isinstance(slots, str) else slots
        return _plain(
            {
                name: getattr(value, name)
                for name in names
                if hasattr(value, name) and getattr(value, name) is not None
            }
        )
    if hasattr(value, "value") and value.__class__.__module__.startswith("langgraph"):
        return _plain(value.value)
    return value


def _principal(request: Request) -> Any:
    return principal_from_scope(request.scope)


def _error(detail: str, status: int = 422) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


def _no_content() -> Response:
    """Return a standards-compliant empty 204 response."""
    return Response(status_code=204)


def _scope(query: Any, model: Any, principal: Any) -> Any:
    if principal is not None:
        query = query.where(
            model.tenant_id == principal.tenant_id,
            model.project_id == principal.project_id,
        )
    return query


def _runtime_context(payload: dict[str, Any], principal: Any) -> dict[str, Any] | None:
    """Persist trusted Agent Server identity for the async worker."""
    del payload
    if principal is None:
        return None
    roles = sorted(principal.roles)
    scopes = sorted(principal.scopes)
    return {
        "user_id": principal.subject,
        "tenant_id": principal.tenant_id,
        "project_id": principal.project_id,
        "role": roles[0] if roles else "user",
        "permissions": scopes,
    }


def _metadata_filter(query: Any, model: Any, metadata: Any) -> Any:
    return query.where(model.metadata_.contains(metadata)) if metadata else query


def _pagination(request: Request, payload: dict[str, Any] | None = None) -> tuple[int, int]:
    source = payload or {}
    try:
        limit = int(source.get("limit", request.query_params.get("limit", 10)))
        offset = int(source.get("offset", request.query_params.get("offset", 0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit and offset must be integers") from exc
    if limit < 1 or limit > 1000 or offset < 0:
        raise ValueError("limit must be between 1 and 1000 and offset cannot be negative")
    return limit, offset


def _assistant(row: AssistantRow) -> dict[str, Any]:
    return _plain(
        {
            "assistant_id": row.assistant_id,
            "graph_id": row.graph_id,
            "config": row.config,
            "context": row.context,
            "metadata": row.metadata_,
            "version": row.version,
            "name": row.name,
            "description": row.description,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def _thread(row: ThreadRow) -> dict[str, Any]:
    value: dict[str, Any] = {
        "thread_id": row.thread_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "metadata": row.metadata_,
        "status": row.status,
        "config": row.config,
        "values": row.values_,
        "state_updated_at": row.state_updated_at,
    }
    if row.interrupts:
        value["interrupts"] = row.interrupts
    return _plain(value)


def _run(row: RunRow) -> dict[str, Any]:
    kwargs = dict(row.kwargs)
    kwargs.pop("runtime_context", None)
    kwargs.pop("runtime_context_token", None)
    return _plain(
        {
            "run_id": row.run_id,
            "thread_id": row.thread_id,
            "assistant_id": row.assistant_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "status": row.status,
            "metadata": row.metadata_,
            "kwargs": kwargs,
            "multitask_strategy": row.multitask_strategy or "enqueue",
        }
    )


def _cron(row: CronRow) -> dict[str, Any]:
    return _plain(
        {
            "cron_id": row.cron_id,
            "assistant_id": row.assistant_id,
            "thread_id": row.thread_id,
            "on_run_completed": row.on_run_completed,
            "end_time": row.end_time,
            "schedule": row.schedule,
            "timezone": row.timezone,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "payload": row.payload,
            "user_id": row.user_id,
            "next_run_date": row.next_run_date,
            "metadata": row.metadata_,
            "enabled": row.enabled,
        }
    )


async def assistants_search(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    try:
        limit, offset = _pagination(request, payload)
    except ValueError as exc:
        return _error(str(exc))
    query = _scope(
        select(AssistantRow).order_by(AssistantRow.created_at.desc()), AssistantRow, principal
    )
    query = _metadata_filter(query, AssistantRow, payload.get("metadata"))
    if payload.get("graph_id"):
        query = query.where(AssistantRow.graph_id == str(payload["graph_id"]))
    if payload.get("name"):
        query = query.where(AssistantRow.name.ilike(f"%{payload['name']}%"))
    async with connect() as conn:
        rows = (await conn.session.execute(query.limit(limit).offset(offset))).scalars().all()
    values = [_assistant(row) for row in rows]
    if payload.get("response_format") == "object":
        return JSONResponse({"assistants": values, "next": None})
    return JSONResponse(values)


async def assistants_count(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    query = _scope(select(func.count()).select_from(AssistantRow), AssistantRow, principal)
    query = _metadata_filter(query, AssistantRow, payload.get("metadata"))
    if payload.get("graph_id"):
        query = query.where(AssistantRow.graph_id == str(payload["graph_id"]))
    if payload.get("name"):
        query = query.where(AssistantRow.name.ilike(f"%{payload['name']}%"))
    async with connect() as conn:
        count = int(await conn.session.scalar(query) or 0)
    return JSONResponse(count)


async def assistants_create(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    if error := scope_override_error(payload, principal):
        return _error(error, 403)
    graph_id = str(payload.get("graph_id") or "")
    if not graph_id:
        return _error("graph_id is required")
    registry = getattr(request.app.state, "graph_registry", None)
    if registry is not None:
        try:
            registry.get(graph_id)
        except KeyError:
            return _error("graph not found", 404)
    assistant_id = UUID(str(payload["assistant_id"])) if payload.get("assistant_id") else uuid4()
    async with connect() as conn:
        existing = await conn.session.get(AssistantRow, assistant_id)
        if existing is not None:
            if payload.get("if_exists") == "do_nothing" and in_principal_scope(existing, principal):
                return JSONResponse(_assistant(existing))
            return _error("assistant already exists", 409)
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
            version=1,
        )
        conn.session.add(row)
        conn.session.add(
            AssistantVersionRow(
                assistant_id=assistant_id,
                version=1,
                graph_id=graph_id,
                config=row.config,
                context=row.context,
                metadata_=row.metadata_,
                name=row.name,
                description=row.description,
            )
        )
        await conn.session.flush()
    return JSONResponse(_assistant(row))


async def assistants_get(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        assistant_id = UUID(request.path_params["assistant_id"])
    except ValueError:
        return _error("assistant not found", 404)
    async with connect() as conn:
        row = await conn.session.get(AssistantRow, assistant_id)
        if row is None or not in_principal_scope(row, principal):
            return _error("assistant not found", 404)
    return JSONResponse(_assistant(row))


async def assistants_graph(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        assistant_id = UUID(request.path_params["assistant_id"])
    except ValueError:
        return _error("assistant not found", 404)
    async with connect() as conn:
        row = await conn.session.get(AssistantRow, assistant_id)
    registry = getattr(request.app.state, "graph_registry", None)
    if row is None or not in_principal_scope(row, principal) or registry is None:
        return _error("assistant not found", 404)
    try:
        xray_value = request.query_params.get("xray", "false")
        xray: int | bool = int(xray_value) if xray_value.isdigit() else xray_value.lower() == "true"
        async with registry.open(
            row.graph_id, {"configurable": {"graph_id": row.graph_id}}
        ) as graph:
            return JSONResponse(_plain(graph.get_graph(xray=xray).to_json()))
    except (KeyError, ValueError) as exc:
        return _error(str(exc), 404)


def _schema_json(schema_type: Any) -> dict[str, Any] | None:
    try:
        return schema_type.model_json_schema()
    except AttributeError:
        try:
            return schema_type.schema()
        except AttributeError:
            return None


def _graph_schema(graph: Any, method: str) -> dict[str, Any] | None:
    try:
        return _schema_json(getattr(graph, method)())
    except PydanticUserError:  # typing.TypedDict schemas need typing_extensions on Python 3.11.
        return {}


async def assistants_schemas(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        assistant_id = UUID(request.path_params["assistant_id"])
    except ValueError:
        return _error("assistant not found", 404)
    async with connect() as conn:
        row = await conn.session.get(AssistantRow, assistant_id)
    registry = getattr(request.app.state, "graph_registry", None)
    if row is None or not in_principal_scope(row, principal) or registry is None:
        return _error("assistant not found", 404)
    try:
        async with registry.open(
            row.graph_id, {"configurable": {"graph_id": row.graph_id}}
        ) as graph:
            return JSONResponse(
                {
                    "graph_id": row.graph_id,
                    "input_schema": _graph_schema(graph, "get_input_schema"),
                    "output_schema": _graph_schema(graph, "get_output_schema"),
                    "state_schema": _graph_schema(graph, "get_input_schema"),
                    "config_schema": None,
                    "context_schema": graph.get_context_jsonschema(),
                }
            )
    except KeyError:
        return _error("assistant not found", 404)


async def assistants_subgraphs(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        assistant_id = UUID(request.path_params["assistant_id"])
    except ValueError:
        return _error("assistant not found", 404)
    async with connect() as conn:
        row = await conn.session.get(AssistantRow, assistant_id)
    registry = getattr(request.app.state, "graph_registry", None)
    if row is None or not in_principal_scope(row, principal) or registry is None:
        return _error("assistant not found", 404)
    try:
        namespace = request.path_params.get("namespace")
        async with registry.open(
            row.graph_id, {"configurable": {"graph_id": row.graph_id}}
        ) as graph:
            return JSONResponse(
                {
                    name: _plain(subgraph.get_graph().to_json())
                    for name, subgraph in graph.get_subgraphs(
                        namespace=namespace,
                        recurse=request.query_params.get("recurse", "false").lower() == "true",
                    )
                }
            )
    except KeyError:
        return _error("assistant not found", 404)


async def assistants_update(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        assistant_id = UUID(request.path_params["assistant_id"])
    except ValueError:
        return _error("assistant not found", 404)
    payload = await request.json()
    if error := scope_override_error(payload, principal):
        return _error(error, 403)
    async with connect() as conn:
        row = await conn.session.get(AssistantRow, assistant_id)
        if row is None or not in_principal_scope(row, principal):
            return _error("assistant not found", 404)
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
    return JSONResponse(_assistant(row))


async def assistants_delete(request: Request) -> JSONResponse | Response:
    principal = _principal(request)
    try:
        assistant_id = UUID(request.path_params["assistant_id"])
    except ValueError:
        return _no_content()
    async with connect() as conn:
        row = await conn.session.get(AssistantRow, assistant_id)
        if row is None or not in_principal_scope(row, principal):
            return _no_content()
        await conn.session.delete(row)
        await conn.session.flush()
    return _no_content()


async def assistants_versions(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        assistant_id = UUID(request.path_params["assistant_id"])
    except ValueError:
        return _error("assistant not found", 404)
    payload = await request.json()
    try:
        limit, offset = _pagination(request, payload)
    except ValueError as exc:
        return _error(str(exc))
    async with connect() as conn:
        assistant = await conn.session.get(AssistantRow, assistant_id)
        if assistant is None or not in_principal_scope(assistant, principal):
            return _error("assistant not found", 404)
        query = (
            select(AssistantVersionRow)
            .where(AssistantVersionRow.assistant_id == assistant_id)
            .order_by(AssistantVersionRow.version.desc())
        )
        rows = (await conn.session.execute(query.limit(limit).offset(offset))).scalars().all()
    return JSONResponse(
        [
            _plain(
                {
                    "assistant_id": row.assistant_id,
                    "version": row.version,
                    "graph_id": row.graph_id,
                    "config": row.config,
                    "context": row.context,
                    "metadata": row.metadata_,
                    "name": row.name,
                    "description": row.description,
                    "created_at": row.created_at,
                }
            )
            for row in rows
        ]
    )


async def assistants_latest(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        assistant_id = UUID(request.path_params["assistant_id"])
    except ValueError:
        return _error("assistant not found", 404)
    payload = await request.json()
    try:
        version = int(payload.get("version"))
    except (TypeError, ValueError):
        return _error("version must be an integer")
    async with connect() as conn:
        row = await conn.session.get(AssistantRow, assistant_id)
        version_row = await conn.session.get(AssistantVersionRow, (assistant_id, version))
        if row is None or version_row is None or not in_principal_scope(row, principal):
            return _error("assistant not found", 404)
        row.version = version
        row.graph_id = version_row.graph_id
        row.config = version_row.config
        row.context = version_row.context
        row.metadata_ = version_row.metadata_
        row.name = version_row.name
        row.description = version_row.description
        row.updated_at = datetime.now(UTC)
        await conn.session.flush()
    return JSONResponse(_assistant(row))


async def threads_create(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    if error := scope_override_error(payload, principal):
        return _error(error, 403)
    thread_id = UUID(str(payload["thread_id"])) if payload.get("thread_id") else uuid4()
    metadata = dict(payload.get("metadata") or {})
    graph_id = payload.get("graph_id") or metadata.get("graph_id")
    async with connect() as conn:
        existing = await conn.session.get(ThreadRow, thread_id)
        if existing is not None:
            if payload.get("if_exists") == "do_nothing" and in_principal_scope(existing, principal):
                return JSONResponse(_thread(existing))
            return _error("thread already exists", 409)
        row = ThreadRow(
            thread_id=thread_id,
            tenant_id=principal.tenant_id if principal else payload.get("tenant_id"),
            project_id=principal.project_id if principal else payload.get("project_id"),
            graph_id=str(graph_id) if graph_id else None,
            status="idle",
            metadata_=metadata,
            config=payload.get("config") or {},
            interrupts={},
            state_updated_at=datetime.now(UTC),
        )
        conn.session.add(row)
        await conn.session.flush()
    return JSONResponse(_thread(row))


async def threads_search(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    try:
        limit, offset = _pagination(request, payload)
    except ValueError as exc:
        return _error(str(exc))
    query = _scope(select(ThreadRow).order_by(ThreadRow.updated_at.desc()), ThreadRow, principal)
    query = _metadata_filter(query, ThreadRow, payload.get("metadata"))
    if payload.get("status"):
        query = query.where(ThreadRow.status == str(payload["status"]))
    if payload.get("ids"):
        try:
            ids = [UUID(str(item)) for item in payload["ids"]]
        except (TypeError, ValueError):
            return _error("ids must contain UUIDs")
        query = query.where(ThreadRow.thread_id.in_(ids))
    if payload.get("values"):
        query = query.where(ThreadRow.values_.contains(payload["values"]))
    async with connect() as conn:
        rows = (await conn.session.execute(query.limit(limit).offset(offset))).scalars().all()
    return JSONResponse([_thread(row) for row in rows])


async def threads_count(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    query = _scope(select(func.count()).select_from(ThreadRow), ThreadRow, principal)
    query = _metadata_filter(query, ThreadRow, payload.get("metadata"))
    if payload.get("status"):
        query = query.where(ThreadRow.status == str(payload["status"]))
    if payload.get("values"):
        query = query.where(ThreadRow.values_.contains(payload["values"]))
    async with connect() as conn:
        count = int(await conn.session.scalar(query) or 0)
    return JSONResponse(count)


async def _get_thread(request: Request) -> tuple[ThreadRow | None, Any, UUID | None]:
    principal = _principal(request)
    try:
        thread_id = UUID(request.path_params["thread_id"])
    except (KeyError, ValueError):
        return None, principal, None
    async with connect() as conn:
        row = await conn.session.get(ThreadRow, thread_id)
    return (
        (row if row is not None and in_principal_scope(row, principal) else None),
        principal,
        thread_id,
    )


async def threads_get(request: Request) -> JSONResponse:
    row, _, _ = await _get_thread(request)
    return JSONResponse(_thread(row)) if row is not None else _error("thread not found", 404)


async def threads_update(request: Request) -> JSONResponse | Response:
    row, principal, _ = await _get_thread(request)
    if row is None:
        return _error("thread not found", 404)
    payload = await request.json()
    if error := scope_override_error(payload, principal):
        return _error(error, 403)
    if not isinstance(payload.get("metadata"), dict):
        return _error("metadata must be an object")
    async with connect() as conn:
        stored = await conn.session.get(ThreadRow, row.thread_id)
        if stored is None or not in_principal_scope(stored, principal):
            return _error("thread not found", 404)
        stored.metadata_ = {**stored.metadata_, **payload["metadata"]}
        stored.updated_at = datetime.now(UTC)
        await conn.session.flush()
        result = _thread(stored)
    if "return=minimal" in request.headers.get("prefer", ""):
        return _no_content()
    return JSONResponse(result)


async def threads_delete(request: Request) -> JSONResponse | Response:
    row, principal, thread_id = await _get_thread(request)
    if row is None or thread_id is None:
        return _no_content()
    async with connect() as conn:
        stored = await conn.session.get(ThreadRow, thread_id)
        if stored is not None and in_principal_scope(stored, principal):
            await conn.session.delete(stored)
            await conn.session.flush()
    await delete_thread_checkpoints(str(thread_id))
    return _no_content()


def _checkpoint_config(thread_id: UUID, checkpoint_id: str | None = None) -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": str(thread_id)}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _checkpoint_key(config: dict[str, Any]) -> dict[str, Any]:
    configurable = config.get("configurable", config)
    return {
        "thread_id": configurable.get("thread_id"),
        "checkpoint_ns": configurable.get("checkpoint_ns", ""),
        "checkpoint_id": configurable.get("checkpoint_id"),
        **(
            {"checkpoint_map": configurable["checkpoint_map"]}
            if "checkpoint_map" in configurable
            else {}
        ),
    }


def _state_from_tuple(item: Any) -> dict[str, Any]:
    checkpoint = item.checkpoint or {}
    values = checkpoint.get("channel_values", checkpoint.get("values", {}))
    tasks = checkpoint.get("tasks", ()) or ()
    interrupts: list[Any] = []
    for task in tasks:
        for interrupt in getattr(task, "interrupts", ()) or ():
            interrupts.append(_plain(interrupt))
    metadata = dict(item.metadata or {})
    return _plain(
        {
            "values": values,
            "next": checkpoint.get("next", ()),
            "checkpoint": _checkpoint_key(item.config),
            "metadata": metadata,
            "created_at": checkpoint.get("ts"),
            "parent_checkpoint": _checkpoint_key(item.parent_config)
            if item.parent_config
            else None,
            "tasks": [_plain(task) for task in tasks],
            "interrupts": interrupts,
        }
    )


def _has_projected_values(values: Any) -> bool:
    """Return whether a checkpoint contains user-visible channel values."""
    return isinstance(values, dict) and any(key != "__pregel_tasks" for key in values)


async def threads_state(request: Request) -> JSONResponse:
    row, _, thread_id = await _get_thread(request)
    if row is None or thread_id is None:
        return _error("thread not found", 404)
    checkpoint_id = request.path_params.get("checkpoint_id")
    try:
        item = await get_checkpointer().aget_tuple(
            cast(RunnableConfig, _checkpoint_config(thread_id, checkpoint_id))
        )
    except Exception as exc:
        return _error(f"checkpoint read failed: {exc}", 503)
    if item is None:
        return JSONResponse(
            _plain(
                {
                    "values": row.values_ or {},
                    "next": [],
                    "checkpoint": _checkpoint_key(_checkpoint_config(thread_id, checkpoint_id)),
                    "metadata": {},
                    "created_at": None,
                    "parent_checkpoint": None,
                    "tasks": [],
                    "interrupts": [],
                }
            )
        )
    state = _state_from_tuple(item)
    # Some LangChain/Deep Agents checkpoints keep only scheduler bookkeeping in
    # channel_values while the durable thread row has the final projected state.
    # Expose that state instead of returning the misleading bare __pregel_tasks.
    if checkpoint_id is None and not _has_projected_values(state.get("values")) and row.values_:
        state["values"] = _plain(row.values_)
    return JSONResponse(state)


async def threads_history(request: Request) -> JSONResponse:
    row, _, thread_id = await _get_thread(request)
    if row is None or thread_id is None:
        return _error("thread not found", 404)
    payload: dict[str, Any] = {}
    if request.method == "POST":
        payload = await request.json()
    try:
        limit, _ = _pagination(request, payload)
    except ValueError as exc:
        return _error(str(exc))
    config = cast(RunnableConfig, _checkpoint_config(thread_id))
    before = (
        payload.get("before") if request.method == "POST" else request.query_params.get("before")
    )
    if request.method == "GET" and before:
        try:
            before = json.loads(before)
        except (TypeError, json.JSONDecodeError):
            return _error("before must be a JSON object")
    if isinstance(before, dict):
        before = {"configurable": before}
    before_config = cast(RunnableConfig | None, before)
    items = []
    async for item in get_checkpointer().alist(config, before=before_config, limit=limit):
        items.append(_state_from_tuple(item))
    return JSONResponse(items)


async def threads_update_state(request: Request) -> JSONResponse | Response:
    row, principal, thread_id = await _get_thread(request)
    if row is None or thread_id is None:
        return _error("thread not found", 404)
    payload = await request.json()
    if request.method == "PATCH":
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return _error("metadata must be an object")
        async with connect() as conn:
            stored = await conn.session.get(ThreadRow, thread_id)
            if stored is None or not in_principal_scope(stored, principal):
                return _error("thread not found", 404)
            stored.metadata_ = {**stored.metadata_, **metadata}
            stored.updated_at = datetime.now(UTC)
            await conn.session.flush()
        return _no_content()
    graph_id = row.graph_id or row.metadata_.get("graph_id")
    registry = getattr(request.app.state, "graph_registry", None)
    if not graph_id or registry is None:
        return _error("thread has no registered graph", 422)
    try:
        graph_id = str(graph_id)
        registry.get(graph_id)
    except KeyError:
        return _error("graph not found", 404)
    config = _checkpoint_config(thread_id, payload.get("checkpoint_id"))
    try:
        async with registry.open(graph_id, config) as graph:
            next_config = await graph.aupdate_state(
                config,
                payload.get("values"),
                as_node=payload.get("as_node"),
            )
    except Exception as exc:
        return _error(f"state update failed: {exc}", 422)
    async with connect() as conn:
        stored = await conn.session.get(ThreadRow, thread_id)
        if stored is not None and in_principal_scope(stored, principal):
            stored.updated_at = datetime.now(UTC)
            stored.state_updated_at = stored.updated_at
            await conn.session.flush()
    return JSONResponse({"checkpoint": _checkpoint_key(next_config)})


async def threads_copy(request: Request) -> JSONResponse:
    row, _, thread_id = await _get_thread(request)
    if row is None or thread_id is None:
        return _error("thread not found", 404)
    target_id = uuid4()
    async with connect() as conn:
        copied = ThreadRow(
            thread_id=target_id,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            graph_id=row.graph_id,
            status=row.status,
            metadata_=dict(row.metadata_),
            config=dict(row.config),
            values_=row.values_,
            interrupts=dict(row.interrupts),
            error=row.error,
        )
        conn.session.add(copied)
        await conn.session.flush()
    try:
        await copy_thread_checkpoints(str(thread_id), str(target_id))
    except Exception:
        async with connect() as conn:
            stored = await conn.session.get(ThreadRow, target_id)
            if stored is not None:
                await conn.session.delete(stored)
                await conn.session.flush()
        raise
    return JSONResponse(_thread(copied), status_code=201)


async def threads_prune(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    ids = payload.get("thread_ids") or []
    try:
        thread_ids = [UUID(str(item)) for item in ids]
    except (TypeError, ValueError):
        return _error("thread_ids must contain UUIDs")
    deleted = 0
    async with connect() as conn:
        query = _scope(
            select(ThreadRow).where(ThreadRow.thread_id.in_(thread_ids)), ThreadRow, principal
        )
        rows = (await conn.session.execute(query)).scalars().all()
        if payload.get("strategy", "delete") == "delete":
            deleted = len(rows)
            for row in rows:
                await conn.session.delete(row)
            await conn.session.flush()
    if payload.get("strategy", "delete") == "delete":
        for thread_id in [row.thread_id for row in rows]:
            await delete_thread_checkpoints(str(thread_id))
    else:
        await get_checkpointer().aprune([str(item) for item in thread_ids], strategy="keep_latest")
        deleted = len(rows)
    return JSONResponse({"pruned_count": deleted})


async def _resolve_assistant(
    request: Request, session: Any, assistant_value: str, principal: Any
) -> AssistantRow | None:
    try:
        assistant_id = UUID(assistant_value)
    except ValueError:
        assistant_id = None
    query = select(AssistantRow)
    if assistant_id is not None:
        query = query.where(AssistantRow.assistant_id == assistant_id)
    else:
        query = query.where(AssistantRow.graph_id == assistant_value)
    query = _scope(query, AssistantRow, principal)
    row = (await session.execute(query.limit(1))).scalar_one_or_none()
    if row is not None or assistant_id is not None:
        return row
    registry = getattr(request.app.state, "graph_registry", None)
    if registry is None:
        return None
    try:
        registry.get(assistant_value)
    except KeyError:
        return None
    deterministic_id = uuid5(
        NAMESPACE_URL,
        f"graphharbor:{getattr(principal, 'tenant_id', '')}:{getattr(principal, 'project_id', '')}:{assistant_value}",
    )
    row = AssistantRow(
        assistant_id=deterministic_id,
        tenant_id=principal.tenant_id if principal else None,
        project_id=principal.project_id if principal else None,
        graph_id=assistant_value,
        name=assistant_value,
        config={},
        context={},
        metadata_={},
        version=1,
    )
    session.add(row)
    session.add(
        AssistantVersionRow(
            assistant_id=deterministic_id,
            version=1,
            graph_id=assistant_value,
            config={},
            context={},
            metadata_={},
            name=assistant_value,
        )
    )
    await session.flush()
    return row


async def _thread_for_run(
    request: Request, session: Any, thread_value: str | None, principal: Any, create: bool = False
) -> ThreadRow | None:
    if thread_value is None:
        return None
    try:
        thread_id = UUID(str(thread_value))
    except ValueError:
        return None
    thread = await session.get(ThreadRow, thread_id)
    if thread is None and create:
        thread = ThreadRow(
            thread_id=thread_id,
            tenant_id=principal.tenant_id if principal else None,
            project_id=principal.project_id if principal else None,
            status="idle",
            metadata_={},
            config={},
            interrupts={},
        )
        session.add(thread)
        await session.flush()
    return thread if thread is not None and in_principal_scope(thread, principal) else None


async def runs_create(
    request: Request,
    *,
    thread_value: str | None = None,
    payload: dict[str, Any] | None = None,
) -> JSONResponse:
    principal = _principal(request)
    payload = payload if payload is not None else await request.json()
    if payload.get("input") is not None and payload.get("command") is not None:
        return _error("input and command cannot be combined")
    if error := scope_override_error(payload, principal):
        return _error(error, 403)
    assistant_value = str(payload.get("assistant_id") or "")
    if not assistant_value:
        return _error("assistant_id is required")
    async with connect() as conn:
        thread = await _thread_for_run(
            request,
            conn.session,
            thread_value,
            principal,
            create=payload.get("if_not_exists") == "create",
        )
        if thread_value is not None and thread is None:
            return _error("thread not found", 404)
        assistant = await _resolve_assistant(request, conn.session, assistant_value, principal)
        if assistant is None:
            return _error("assistant not found", 404)
        run_payload = dict(payload)
        run_payload.pop("runtime_context", None)
        run_payload.pop("runtime_context_token", None)
        run_context = payload.get("context")
        assistant_context = assistant.context if isinstance(assistant.context, dict) else {}
        if isinstance(run_context, dict):
            run_payload["context"] = {**assistant_context, **run_context}
        elif "context" not in payload and assistant_context:
            run_payload["context"] = dict(assistant_context)
        trusted_context = _runtime_context(run_payload, principal)
        if trusted_context is not None:
            run_payload["runtime_context"] = trusted_context
        policy = getattr(principal, "policy", None)
        if os.environ.get("GRAPHHARBOR_ENV", "development") == "production" and policy is None:
            return _error("delegation runtime policy is required", 401)
        requested_config = run_payload.get("config")
        requested_configurable = (
            requested_config.get("configurable") if isinstance(requested_config, dict) else None
        )
        try:
            validate_policy_overrides(
                policy,
                configurable=requested_configurable,
                context=run_payload.get("context")
                if isinstance(run_payload.get("context"), dict)
                else None,
            )
        except RuntimeContextError as exc:
            return _error(str(exc), 403)
        idempotency_key = request.headers.get("idempotency-key") or payload.get("idempotency_key")
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant.assistant_id,
            thread_id=thread.thread_id if thread else None,
            kwargs=run_payload,
            metadata=payload.get("metadata") or {},
            tenant_id=principal.tenant_id if principal else getattr(thread, "tenant_id", None),
            project_id=principal.project_id if principal else getattr(thread, "project_id", None),
            idempotency_key=idempotency_key,
            multitask_strategy=str(payload.get("multitask_strategy") or "enqueue"),
        )
        if trusted_context and not run.kwargs.get("runtime_context_token"):
            run.kwargs = {
                **run.kwargs,
                "runtime_context_token": sign_runtime_context(
                    trusted_context,
                    run_id=str(run.run_id),
                    thread_id=str(thread.thread_id) if thread else None,
                    policy=policy,
                ),
            }
            run.kwargs.pop("runtime_context", None)
            await conn.session.flush()
        await conn.session.refresh(run)
        conn.schedule_after_commit(lambda run_id=run.run_id: enqueue_run(run_id))
        response = _run(run)
    metric_inc("graphharbor_runs_created_total")
    return JSONResponse(response, status_code=201)


async def runs_create_root(request: Request) -> JSONResponse:
    return await runs_create(request)


async def runs_create_thread(request: Request) -> JSONResponse:
    return await runs_create(request, thread_value=request.path_params.get("thread_id"))


async def runs_list(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        thread_id = UUID(request.path_params["thread_id"])
    except ValueError:
        return _error("thread not found", 404)
    try:
        limit, offset = _pagination(request)
    except ValueError as exc:
        return _error(str(exc))
    query = _scope(
        select(RunRow).where(RunRow.thread_id == thread_id).order_by(RunRow.created_at.desc()),
        RunRow,
        principal,
    )
    if request.query_params.get("status"):
        query = query.where(RunRow.status == request.query_params["status"])
    async with connect() as conn:
        rows = (await conn.session.execute(query.limit(limit).offset(offset))).scalars().all()
    return JSONResponse([_run(row) for row in rows])


async def runs_get(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        thread_id = UUID(request.path_params["thread_id"])
        run_id = UUID(request.path_params["run_id"])
    except ValueError:
        return _error("run not found", 404)
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        if row is None or row.thread_id != thread_id or not in_principal_scope(row, principal):
            return _error("run not found", 404)
    return JSONResponse(_run(row))


async def runs_delete(request: Request) -> JSONResponse | Response:
    principal = _principal(request)
    try:
        thread_id = UUID(request.path_params["thread_id"])
        run_id = UUID(request.path_params["run_id"])
    except ValueError:
        return _no_content()
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        if row is not None and row.thread_id == thread_id and in_principal_scope(row, principal):
            await conn.session.delete(row)
            await conn.session.flush()
    return _no_content()


async def _cancel_row(request: Request, conn: Any, row: RunRow, action: str) -> None:
    locked = await conn.session.scalar(
        select(RunRow).where(RunRow.run_id == row.run_id).with_for_update()
    )
    if locked is None:
        return
    row = locked
    if action == "rollback":
        thread_id = row.thread_id
        await conn.session.delete(row)
        await conn.session.flush()
        if thread_id:
            conn.schedule_after_commit(lambda: delete_thread_checkpoints(str(thread_id)))
        return
    if is_terminal(row.status):
        return
    change = transition(
        row.status,
        RunStatus.INTERRUPTED,
        reason=RunReason.CANCEL_REQUESTED,
        retry_count=row.retry_count,
    )
    row.status = change.status.value
    row.reason = change.reason.value
    row.lease_owner = None
    row.lease_expires_at = None
    row.heartbeat_at = datetime.now(UTC)
    row.updated_at = datetime.now(UTC)
    await conn.session.execute(delete(RunLeaseRow).where(RunLeaseRow.run_id == row.run_id))
    if row.thread_id is not None:
        thread = await conn.session.get(ThreadRow, row.thread_id)
        if thread is not None:
            thread.status = "idle"
    await conn.session.flush()
    durable = await RunRepository().record_event(
        conn.session,
        run_id=row.run_id,
        thread_id=row.thread_id,
        topic="lifecycle",
        payload={
            "event": "lifecycle",
            "status": RunStatus.INTERRUPTED.value,
            "reason": RunReason.CANCEL_REQUESTED.value,
        },
        namespace=[],
        trace_context={
            "assistant_id": str(row.assistant_id),
        },
        terminal=True,
    )
    conn.schedule_after_commit(lambda: _fanout_durable_event(durable))
    if row.thread_id:

        async def publish() -> None:
            try:
                await get_stream_manager().put(
                    row.run_id,
                    row.thread_id,
                    Message(topic=b"run:control", data=json.dumps({"action": action}).encode()),
                )
            except RuntimeError:
                pass

        conn.schedule_after_commit(publish)


async def _fanout_durable_event(event: RuntimeEventRow) -> None:
    """Publish an already-committed terminal event without making Redis authoritative."""
    try:
        manager = get_stream_manager()
        run_id = event.run_id
        thread_id = event.thread_id
        if run_id is None:
            return
        payload = {
            "id": str(event.event_id),
            "seq": event.sequence,
            "run_id": str(run_id),
            "thread_id": str(thread_id) if thread_id else None,
            "event": event.payload,
        }
        await manager.put(
            run_id,
            thread_id,
            Message(
                topic=b"event:lifecycle",
                data=json.dumps(payload, separators=(",", ":"), default=str).encode(),
            ),
            resumable=True,
        )
        if thread_id is not None:
            wire = protocol_event(
                event_id=str(event.event_id),
                sequence=event.sequence,
                run_id=str(run_id),
                thread_id=str(thread_id),
                event=event.payload,
            )
            await manager.put_thread(
                thread_id,
                Message(
                    topic=f"protocol:{wire['method']}".encode(),
                    data=json.dumps(wire, separators=(",", ":"), default=str).encode(),
                ),
            )
    except Exception:
        # PostgreSQL replay remains available after Redis recovers.
        return


async def runs_cancel(request: Request) -> JSONResponse:
    principal = _principal(request)
    action = request.query_params.get("action", "interrupt")
    if action not in {"interrupt", "rollback"}:
        return _error("action must be interrupt or rollback")
    try:
        thread_id = UUID(request.path_params["thread_id"])
        run_id = UUID(request.path_params["run_id"])
    except ValueError:
        return _error("run not found", 404)
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        if row is None or row.thread_id != thread_id or not in_principal_scope(row, principal):
            return _error("run not found", 404)
        await _cancel_row(request, conn, row, action)
        response = {} if action == "rollback" else _run(row)
    if request.query_params.get("wait") in {"1", "true", "True"} and response:
        response = await _wait_for_run(thread_id, run_id, principal)
    metric_inc("graphharbor_runs_cancel_requested_total", labels={"action": action})
    return JSONResponse(response)


async def runs_cancel_many(request: Request) -> JSONResponse:
    principal = _principal(request)
    action = request.query_params.get("action", "interrupt")
    if action not in {"interrupt", "rollback"}:
        return _error("action must be interrupt or rollback")
    payload = await request.json()
    query = _scope(select(RunRow), RunRow, principal)
    if payload.get("thread_id"):
        try:
            query = query.where(RunRow.thread_id == UUID(str(payload["thread_id"])))
        except ValueError:
            return _error("thread_id must be a UUID")
    if payload.get("run_ids"):
        try:
            query = query.where(RunRow.run_id.in_([UUID(str(item)) for item in payload["run_ids"]]))
        except (TypeError, ValueError):
            return _error("run_ids must contain UUIDs")
    status = payload.get("status")
    if status == "pending":
        query = query.where(RunRow.status == RunStatus.PENDING.value)
    elif status == "running":
        query = query.where(RunRow.status == RunStatus.RUNNING.value)
    elif status not in {None, "all"}:
        return _error("status must be pending, running, or all")
    async with connect() as conn:
        rows = (await conn.session.execute(query)).scalars().all()
        for row in rows:
            await _cancel_row(request, conn, row, action)
    metric_inc("graphharbor_runs_cancel_requested_total", len(rows), labels={"action": action})
    return JSONResponse({})


async def _wait_for_run(thread_id: UUID | None, run_id: UUID, principal: Any) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + float(
        __import__("os").environ.get("GRAPHHARBOR_RUN_WAIT_TIMEOUT", "60")
    )
    while asyncio.get_running_loop().time() < deadline:
        async with connect() as conn:
            row = await conn.session.get(RunRow, run_id)
            if row is None or not in_principal_scope(row, principal):
                return {"detail": "run not found"}
            if is_terminal(row.status):
                if thread_id is not None:
                    thread = await conn.session.get(ThreadRow, thread_id)
                    return _thread(thread) if thread is not None else _run(row)
                return _run(row)
        await asyncio.sleep(0.1)
    return {"detail": "run wait timed out", "run_id": str(run_id)}


async def runs_wait(request: Request) -> JSONResponse:
    thread_value = request.path_params.get("thread_id")
    return await _runs_wait_impl(request, thread_value)


async def runs_wait_root(request: Request) -> JSONResponse:
    return await _runs_wait_impl(request, None)


async def _runs_wait_impl(request: Request, thread_value: str | None) -> JSONResponse:
    created = await runs_create(request, thread_value=thread_value)
    if created.status_code >= 300:
        return created
    try:
        run_id = UUID(str(json.loads(bytes(created.body))["run_id"]))
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return _error("run creation returned an invalid run", 500)
    thread_id = UUID(thread_value) if thread_value else None
    result = await _wait_for_run(thread_id, run_id, _principal(request))
    return JSONResponse(result)


async def runs_join(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        thread_id = UUID(request.path_params["thread_id"])
        run_id = UUID(request.path_params["run_id"])
    except ValueError:
        return _error("run not found", 404)
    return JSONResponse(await _wait_for_run(thread_id, run_id, principal))


async def runs_batch(request: Request) -> JSONResponse:
    payloads = await request.json()
    if not isinstance(payloads, list):
        return _error("runs batch payload must be an array")
    results: list[dict[str, Any]] = []
    for payload in payloads:
        result = await runs_create(
            request,
            thread_value=payload.get("thread_id"),
            payload=payload,
        )
        if result.status_code >= 300:
            return result
        results.append(json.loads(bytes(result.body)))
    return JSONResponse(results, status_code=201)


async def crons_search(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    try:
        limit, offset = _pagination(request, payload)
    except ValueError as exc:
        return _error(str(exc))
    query = _scope(select(CronRow).order_by(CronRow.created_at.desc()), CronRow, principal)
    if payload.get("assistant_id"):
        try:
            query = query.where(CronRow.assistant_id == UUID(str(payload["assistant_id"])))
        except ValueError:
            return _error("assistant_id must be a UUID")
    if payload.get("thread_id"):
        try:
            query = query.where(CronRow.thread_id == UUID(str(payload["thread_id"])))
        except ValueError:
            return _error("thread_id must be a UUID")
    if payload.get("enabled") is not None:
        query = query.where(CronRow.enabled == bool(payload["enabled"]))
    query = _metadata_filter(query, CronRow, payload.get("metadata"))
    async with connect() as conn:
        rows = (await conn.session.execute(query.limit(limit).offset(offset))).scalars().all()
    return JSONResponse([_cron(row) for row in rows])


async def crons_count(request: Request) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    query = _scope(select(func.count()).select_from(CronRow), CronRow, principal)
    if payload.get("assistant_id"):
        try:
            query = query.where(CronRow.assistant_id == UUID(str(payload["assistant_id"])))
        except ValueError:
            return _error("assistant_id must be a UUID")
    if payload.get("thread_id"):
        try:
            query = query.where(CronRow.thread_id == UUID(str(payload["thread_id"])))
        except ValueError:
            return _error("thread_id must be a UUID")
    query = _metadata_filter(query, CronRow, payload.get("metadata"))
    async with connect() as conn:
        count = int(await conn.session.scalar(query) or 0)
    return JSONResponse(count)


async def cron_create(request: Request, *, thread_value: str | None = None) -> JSONResponse:
    principal = _principal(request)
    payload = await request.json()
    if not payload.get("schedule") or not payload.get("assistant_id"):
        return _error("schedule and assistant_id are required")
    async with connect() as conn:
        assistant = await _resolve_assistant(
            request, conn.session, str(payload["assistant_id"]), principal
        )
        if assistant is None:
            return _error("assistant not found", 404)
        thread = await _thread_for_run(request, conn.session, thread_value, principal)
        if thread_value is not None and thread is None:
            return _error("thread not found", 404)
        end_time = None
        if payload.get("end_time"):
            try:
                end_time = datetime.fromisoformat(str(payload["end_time"]).replace("Z", "+00:00"))
            except ValueError:
                return _error("end_time must be an ISO date-time")
        row = CronRow(
            cron_id=uuid4(),
            tenant_id=principal.tenant_id if principal else None,
            project_id=principal.project_id if principal else None,
            assistant_id=assistant.assistant_id,
            thread_id=thread.thread_id if thread else None,
            schedule=str(payload["schedule"]),
            payload=dict(payload),
            metadata_=payload.get("metadata") or {},
            end_time=end_time,
            timezone=payload.get("timezone"),
            on_run_completed=payload.get("on_run_completed"),
            enabled=bool(payload.get("enabled", True)),
        )
        conn.session.add(row)
        await conn.session.flush()
    return JSONResponse(_cron(row), status_code=200)


async def cron_create_root(request: Request) -> JSONResponse:
    return await cron_create(request)


async def cron_create_thread(request: Request) -> JSONResponse:
    return await cron_create(request, thread_value=request.path_params.get("thread_id"))


async def cron_search(request: Request) -> JSONResponse:
    return await crons_search(request)


async def cron_update(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        cron_id = UUID(request.path_params["cron_id"])
    except ValueError:
        return _error("cron not found", 404)
    payload = await request.json()
    async with connect() as conn:
        row = await conn.session.get(CronRow, cron_id)
        if row is None or not in_principal_scope(row, principal):
            return _error("cron not found", 404)
        for field in ("schedule", "timezone", "on_run_completed", "enabled"):
            if field in payload:
                setattr(row, field, payload[field])
        if "end_time" in payload:
            row.end_time = (
                datetime.fromisoformat(str(payload["end_time"]).replace("Z", "+00:00"))
                if payload["end_time"]
                else None
            )
        if isinstance(payload.get("metadata"), dict):
            row.metadata_ = {**row.metadata_, **payload["metadata"]}
        row.payload = {**row.payload, **payload}
        row.updated_at = datetime.now(UTC)
        await conn.session.flush()
    return JSONResponse(_cron(row))


async def cron_get(request: Request) -> JSONResponse:
    principal = _principal(request)
    try:
        cron_id = UUID(request.path_params["cron_id"])
    except ValueError:
        return _error("cron not found", 404)
    async with connect() as conn:
        row = await conn.session.get(CronRow, cron_id)
        if row is None or not in_principal_scope(row, principal):
            return _error("cron not found", 404)
    return JSONResponse(_cron(row))


async def cron_delete(request: Request) -> JSONResponse | Response:
    principal = _principal(request)
    try:
        cron_id = UUID(request.path_params["cron_id"])
    except ValueError:
        return _no_content()
    async with connect() as conn:
        row = await conn.session.get(CronRow, cron_id)
        if row is not None and in_principal_scope(row, principal):
            await conn.session.delete(row)
            await conn.session.flush()
    return _no_content()


__all__ = [name for name in globals() if not name.startswith("_")]
