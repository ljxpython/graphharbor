"""Measure 10k deterministic message deltas in a disposable PostgreSQL database."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from uuid import uuid4

from sqlalchemy import text

from langgraph_runtime_pg.database import connect, start_pool, stop_pool
from langgraph_runtime_pg.models import AssistantRow, RunRow, ThreadRow
from langgraph_runtime_pg.run_store import RunRepository


async def main() -> None:
    assistant_id, thread_id, run_id = uuid4(), uuid4(), uuid4()
    async with connect() as conn:
        conn.session.add(AssistantRow(assistant_id=assistant_id, graph_id="storage-fixture"))
        conn.session.add(ThreadRow(thread_id=thread_id, metadata_={}, config={}, interrupts={}))
        conn.session.add(
            RunRow(
                run_id=run_id,
                assistant_id=assistant_id,
                thread_id=thread_id,
                status="running",
                kwargs={},
                metadata_={},
            )
        )
    repository = RunRepository()
    start = time.perf_counter()
    for offset in range(0, 10_000, 100):
        events = [
            {
                "event": "messages",
                "data": [
                    {
                        "event": "content-block-delta",
                        "id": "fixture-message",
                        "index": 0,
                        "delta": {"type": "text", "text": f"{offset + i:05d}"},
                    }
                ],
            }
            for i in range(100)
        ]
        async with connect() as conn:
            await repository.record_message_deltas(
                conn.session, run_id=run_id, thread_id=thread_id, events=events
            )
    elapsed = time.perf_counter() - start
    async with connect() as conn:
        rows = await conn.session.execute(
            text(
                "SELECT count(*) AS rows, sum(pg_column_size(payload)) AS payload_bytes, "
                "max(pg_column_size(payload)) AS max_payload_bytes "
                "FROM runtime_events WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        )
        row = rows.one()
        sizes = (
            await conn.session.execute(
                text(
                    "SELECT pg_relation_size('runtime_events') AS heap_bytes, "
                    "pg_total_relation_size('runtime_events') - "
                    "pg_relation_size('runtime_events') - "
                    "pg_indexes_size('runtime_events') AS toast_bytes, "
                    "pg_indexes_size('runtime_events') AS index_bytes"
                )
            )
        ).one()
    sys.stdout.write(
        json.dumps(
            {
                "rows": row.rows,
                "payload_bytes": row.payload_bytes,
                "max_payload_bytes": row.max_payload_bytes,
                "heap_bytes": sizes.heap_bytes,
                "toast_bytes": sizes.toast_bytes,
                "index_bytes": sizes.index_bytes,
                "write_seconds": round(elapsed, 3),
            }
        )
        + "\n"
    )


if __name__ == "__main__":

    async def run() -> None:
        await start_pool()
        try:
            await main()
        finally:
            await stop_pool()

    asyncio.run(run())
