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
    assert trace["event"] == "lifecycle"
    assert trace["reason"] == "completed"
    assert trace["namespace"]["kind"] == "list"
    assert trace["data"]["kind"] == "dict"
    assert trace["output"]["kind"] == "str"
    assert "secret content" not in str(trace)
    assert "raw output" not in str(trace)


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
    }
    assert cast(dict[str, object], captured["payload"])["data"] == {"content": "secret"}
