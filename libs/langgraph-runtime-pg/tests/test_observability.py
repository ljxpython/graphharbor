from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest


def test_build_trace_metadata_redacts_sensitive_payload() -> None:
    from langgraph_runtime_pg.observability import build_trace_metadata

    trace = build_trace_metadata(
        context={
            "run_id": "run-1",
            "thread_id": "thread-1",
            "assistant_id": "assistant-1",
            "graph_id": "assistant",
            "model_id": "model-a",
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "user_id": "user-1",
            "request_id": "request-1",
            "platform_trace_id": "platform-trace-1",
        },
        event={
            "event": "lifecycle",
            "namespace": ["node-a", "node-b"],
            "data": {"content": "secret content", "count": 2},
            "output": "raw output",
            "reason": "completed",
        },
    )

    assert trace["run_id"] == "run-1"
    assert trace["thread_id"] == "thread-1"
    assert trace["assistant_id"] == "assistant-1"
    assert trace["graph_id"] == "assistant"
    assert trace["model_id"] == "model-a"
    assert trace["request_id"] == "request-1"
    assert trace["platform_trace_id"] == "platform-trace-1"
    assert trace["event"] == "lifecycle"
    assert trace["reason"] == "completed"
    assert trace["namespace"]["kind"] == "list"
    assert trace["data"]["kind"] == "dict"
    assert trace["output"]["kind"] == "str"
    assert "secret content" not in str(trace)
    assert "raw output" not in str(trace)


def test_build_trace_metadata_accepts_custom_allowed_keys() -> None:
    from langgraph_runtime_pg.observability import build_trace_metadata

    # Test with custom allowed_keys including business-specific key
    trace = build_trace_metadata(
        context={
            "run_id": "run-1",
            "thread_id": "thread-1",
            "policy_version": "policy-v1",
            "business_key": "business-value",
            "should_not_appear": "ignored",
        },
        allowed_keys=["run_id", "thread_id", "policy_version", "business_key"],
        event={"event": "lifecycle"},
    )

    assert trace["run_id"] == "run-1"
    assert trace["thread_id"] == "thread-1"
    assert trace["policy_version"] == "policy-v1"
    assert trace["business_key"] == "business-value"
    assert "should_not_appear" not in trace

    # Test that default behavior excludes policy_version
    trace_default = build_trace_metadata(
        context={
            "run_id": "run-2",
            "policy_version": "policy-v2",
        },
        event={"event": "lifecycle"},
    )

    assert trace_default["run_id"] == "run-2"
    assert "policy_version" not in trace_default


@pytest.mark.asyncio
async def test_worker_publish_event_forwards_trace_context(monkeypatch) -> None:
    from langgraph_runtime_pg.production_worker import ProductionWorker

    worker = ProductionWorker(cast(Any, SimpleNamespace()), owner="worker-a")
    captured: dict[str, object] = {}

    async def record_event(
        session: object,
        *,
        run_id: UUID | None,
        thread_id: UUID | None,
        topic: str,
        payload: dict[str, object],
        namespace: list[str] | None = None,
        trace_context: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        del session, namespace
        captured["run_id"] = run_id
        captured["thread_id"] = thread_id
        captured["topic"] = topic
        captured["payload"] = payload
        captured["trace_context"] = trace_context
        return SimpleNamespace(
            run_id=run_id,
            thread_id=thread_id,
            topic=topic,
            payload=payload,
            event_id=uuid4(),
            sequence=1,
        )

    @asynccontextmanager
    async def fake_connect():
        yield SimpleNamespace(session=object())

    async def fanout(_: object) -> None:
        return None

    monkeypatch.setattr(worker.repository, "record_event", record_event)
    monkeypatch.setattr("langgraph_runtime_pg.production_worker.connect", fake_connect)
    monkeypatch.setattr(worker, "_fanout_durable_event", fanout)

    run_id = uuid4()
    thread_id = uuid4()
    await worker._publish_event(
        run_id,
        thread_id,
        {"event": "lifecycle", "data": {"content": "secret"}},
        trace_context={
            "assistant_id": "assistant-1",
            "graph_id": "assistant",
            "model_id": "model-a",
            "user_id": "user-1",
            "request_id": "request-1",
            "platform_trace_id": "platform-trace-1",
        },
    )

    assert captured["run_id"] == run_id
    assert captured["thread_id"] == thread_id
    assert captured["topic"] == "lifecycle"
    assert captured["trace_context"] == {
        "assistant_id": "assistant-1",
        "graph_id": "assistant",
        "model_id": "model-a",
        "user_id": "user-1",
        "request_id": "request-1",
        "platform_trace_id": "platform-trace-1",
    }
    assert cast(dict[str, object], captured["payload"])["data"] == {"content": "secret"}


@pytest.mark.asyncio
async def test_worker_run_forwards_trace_context_to_graph_events(monkeypatch) -> None:
    from langgraph_runtime_pg import production_worker as worker_module
    from langgraph_runtime_pg.models import RunRow
    from langgraph_runtime_pg.production_worker import ProductionWorker

    run_id = uuid4()
    assistant_id = uuid4()
    run = SimpleNamespace(
        run_id=run_id,
        thread_id=None,
        assistant_id=assistant_id,
        tenant_id="tenant-1",
        project_id="project-1",
        status="running",
        kwargs={"input": {"value": 1}, "runtime_context_token": "signed"},
        metadata_={},
    )
    assistant = SimpleNamespace(
        graph_id="assistant",
        config={},
        context={},
        metadata_={},
        version=1,
    )
    trace_context = {
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "role": "operator",
        "permissions": [],
        "request_id": "request-1",
        "platform_trace_id": "platform-trace-1",
    }
    events: list[tuple[dict[str, object], dict[str, object] | None]] = []

    class Session:
        async def get(self, model: object, _key: object) -> object | None:
            return run if model is RunRow else None

        async def scalar(self, _query: object) -> object:
            return assistant

        async def flush(self) -> None:
            return None

    class Repository:
        lease_seconds = 60
        last_transition_events: list[object] = []

        async def claim_next(self, _session: object, _owner: str) -> object:
            return run

        async def finish(self, *_args: object, **_kwargs: object) -> object:
            return run

    @asynccontextmanager
    async def fake_connect():
        yield SimpleNamespace(session=Session())

    @asynccontextmanager
    async def open_graph(_graph_id: str, _config: object):
        yield object()

    async def fake_invoke(_graph: object, _input: object, *, on_event, **_kwargs: object):
        await on_event({"event": "values", "data": {"value": 2}})
        return SimpleNamespace(value={"value": 2}, interrupts=())

    async def no_op(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setenv("GRAPHHARBOR_ENV", "development")
    monkeypatch.setattr(worker_module, "connect", fake_connect)
    monkeypatch.setattr(worker_module, "dequeue_run_hint", no_op)
    monkeypatch.setattr(worker_module, "set_run_heartbeat", no_op)
    monkeypatch.setattr(worker_module, "clear_run_heartbeat", no_op)
    monkeypatch.setattr(worker_module, "bg_job_heartbeat_secs", lambda: 1.0)
    monkeypatch.setattr(
        worker_module,
        "verify_runtime_context_envelope",
        lambda *_args, **_kwargs: trace_context,
    )
    monkeypatch.setattr(worker_module, "invoke_graph", fake_invoke)

    worker = ProductionWorker(SimpleNamespace(open=open_graph), owner="worker-a")
    worker.repository = Repository()

    async def publish(
        _run_id: UUID,
        _thread_id: UUID | None,
        event: dict[str, object],
        *,
        trace_context: dict[str, object] | None = None,
    ) -> None:
        events.append((event, trace_context))

    monkeypatch.setattr(worker, "_publish_event", publish)
    assert await worker.run_once()

    assert [event[0]["event"] for event in events] == ["lifecycle", "values"]
    assert (
        events[0][1]
        == events[1][1]
        == {
            "assistant_id": str(assistant_id),
            "assistant_version": "1",
            "graph_id": "assistant",
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "user_id": "user-1",
            "request_id": "request-1",
            "platform_trace_id": "platform-trace-1",
        }
    )
