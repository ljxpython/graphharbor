"""Replay cache expiry protects workers that exit before normal cleanup."""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import redis.asyncio as redis


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["success", "error", "interrupted"])
async def test_replay_cache_expiry_and_terminal_cleanup(monkeypatch, status):
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langgraph_runtime_pg.redis_stream import Message, StreamManager, _stream_key

    monkeypatch.setenv("GRAPHHARBOR_REDIS_PREFIX", f"resource-bounds:{uuid4().hex}")
    client = redis.Redis.from_url(os.environ["REDIS_URI"])
    manager = StreamManager(client)
    thread_id, run_id = uuid4(), uuid4()
    key = _stream_key(thread_id, run_id)
    try:
        await manager.put(run_id, thread_id, Message(b"event:messages", b"one"), True)
        assert 0 < await client.ttl(key) <= 3600
        await client.persist(key)
        await manager.put_batch(run_id, thread_id, [(Message(b"event:messages", b"two"), None)])
        assert await client.xlen(key) == 2
        assert 0 < await client.ttl(key) <= 3600
        worker = ProductionWorker.__new__(ProductionWorker)
        monkeypatch.setattr(
            "langgraph_runtime_pg.production_worker.get_stream_manager", lambda: manager
        )
        clear_buffers = manager.clear_run_buffers

        async def clear_without_grace(run_id, thread_id):
            await clear_buffers(run_id, thread_id, local_grace_secs=0)

        monkeypatch.setattr(manager, "clear_run_buffers", clear_without_grace)
        durable = SimpleNamespace(
            run_id=run_id,
            thread_id=thread_id,
            event_id=uuid4(),
            sequence=3,
            topic="lifecycle",
            payload={"event": "lifecycle", "status": status},
            terminal=False,
        )
        await worker._fanout_durable_event(durable)
        assert not manager._cleanup_tasks
        assert manager.message_stores[thread_id][run_id]
        durable.terminal = True
        await worker._fanout_durable_event(durable)
        assert manager._cleanup_tasks
        await asyncio.gather(*manager._cleanup_tasks)
        assert thread_id not in manager.message_stores
        assert 0 < await client.ttl(key) <= 3600
    finally:
        await manager.aclose_fanout()
        await client.unlink(key)
        await client.aclose()
