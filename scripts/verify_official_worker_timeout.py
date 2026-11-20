"""Probe the pinned official Worker with isolated graph/database sinks."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

os.environ["LANGGRAPH_RUNTIME_EDITION"] = "inmem"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.setdefault("REDIS_URI", "redis://127.0.0.1:1/0")
os.environ.setdefault("DATABASE_URI", "postgresql://127.0.0.1/official_probe_unused")
os.environ.setdefault("LANGSERVE_GRAPHS", "{}")


@asynccontextmanager
async def isolated_context(*args, **kwargs):
    yield object()


async def main():
    from langgraph_api import worker

    assert version("langgraph-api") == "0.13.0"
    run = {
        "run_id": uuid4(),
        "thread_id": uuid4(),
        "assistant_id": uuid4(),
        "created_at": datetime.now(UTC),
        "kwargs": {
            "temporary": True,
            "config": {"configurable": {"graph_id": "probe"}},
        },
    }
    outcomes = []
    consume = AsyncMock()
    run_ops = Mock(enter=isolated_context, set_status=AsyncMock())
    thread_ops = Mock(delete=AsyncMock())
    with (
        patch.object(worker, "connect", isolated_context),
        patch.object(worker, "set_auth_ctx_for_run", isolated_context),
        patch.object(worker, "Runs", run_ops),
        patch.object(worker, "Threads", thread_ops),
        patch.object(worker, "get_otlp_metrics_reporter", Mock(return_value=Mock())),
        patch.object(
            worker,
            "logger",
            Mock(ainfo=AsyncMock(), aerror=AsyncMock(), awarning=AsyncMock()),
        ),
        patch.object(worker, "decrypt_response", AsyncMock(side_effect=lambda data, *args: data)),
        patch.object(worker, "astream_state", Mock(return_value=object())),
        patch.object(worker, "consume", consume),
        patch.object(worker, "BG_JOB_TIMEOUT_SECS", 0.2),
        patch.object(worker, "BG_JOB_MAX_RETRIES", 3),
    ):
        loop = asyncio.get_running_loop()
        consume.side_effect = TimeoutError("provider timeout")
        result = await worker.worker(run, 1, loop)
        assert result["status"] == "error"
        assert isinstance(result["exception"], TimeoutError)
        assert run_ops.set_status.await_count == 0
        outcomes.append("provider timeout -> error, no retry")

        async def slow(*args, **kwargs):
            await asyncio.sleep(1)

        consume.side_effect = slow
        result = await worker.worker(run, 1, loop)
        assert result["status"] == "timeout"
        assert run_ops.set_status.await_count == 0
        outcomes.append("attempt deadline -> timeout, no retry")

        retriable = worker.RETRIABLE_EXCEPTIONS[0]("transient database error")
        consume.side_effect = retriable
        try:
            await worker.worker(run, 1, loop)
        except type(retriable):
            pass
        else:
            raise AssertionError("official worker must requeue a retriable failure")
        assert run_ops.set_status.await_count == 1
        assert run_ops.set_status.await_args.args[1:] == (run["run_id"], "pending")
        await asyncio.sleep(0.25)

        async def completes(*args, **kwargs):
            await asyncio.sleep(0.05)

        consume.side_effect = completes
        result = await worker.worker(run, 2, loop)
        assert result["status"] == "success"
        outcomes.append("retry after prior deadline -> fresh attempt succeeds")
        calls = consume.await_count
        result = await worker.worker(run, 4, loop)
        assert result["status"] == "error" and consume.await_count == calls
        outcomes.append("attempt 4 with BG_JOB_MAX_RETRIES=3 -> error without graph execution")

    source = Path(inspect.getfile(worker))
    print(  # noqa: T201 -- standalone verification report
        json.dumps(
            {
                "langgraph_api": version("langgraph-api"),
                "worker_sha256": hashlib.sha256(
                    await asyncio.to_thread(source.read_bytes)
                ).hexdigest(),
                "checks": outcomes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
