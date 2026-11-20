"""Measure run-boundary capture and pruning in a disposable, explicitly named database."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import resource
import statistics
import sys
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, text

from langgraph_runtime_pg.checkpoint_mutations import capture_baseline, prune_checkpoints
from langgraph_runtime_pg.database import connect, start_pool, stop_pool
from langgraph_runtime_pg.models import RunCheckpointBaselineRow, RunRow, ThreadRow


async def measure(depth: int, payload: int, repeats: int) -> dict:
    tid, rid = uuid4(), uuid4()
    async with connect() as conn:
        database = await conn.session.scalar(text("SELECT current_database()"))
        if database != "graphharbor_event_retention_verify":
            raise RuntimeError(
                "use scripts/run_isolated_tests.py with its default disposable database"
            )
        await conn.session.execute(
            text("SELECT set_config('graphharbor.checkpoint_format', 'journal-v1', true)")
        )
        thread = ThreadRow(thread_id=tid, values_={"items": list(range(depth))})
        run = RunRow(run_id=rid, thread_id=tid, assistant_id=uuid4())
        conn.session.add_all([thread, run])
        await conn.session.flush()
        await conn.session.execute(
            text("""
            INSERT INTO checkpoints(thread_id, checkpoint_ns, checkpoint_id,
                parent_checkpoint_id, checkpoint, metadata)
            SELECT :tid, '', lpad(i::text, 10, '0'),
                CASE WHEN i>1 THEN lpad((i-1)::text,10,'0') END,
                jsonb_build_object('v',4,'id',lpad(i::text,10,'0'),
                    'channel_values','{}'::jsonb,'channel_versions',
                    jsonb_build_object('payload', i::text)), '{}'::jsonb
            FROM generate_series(1,:depth) i
        """),
            {"tid": str(tid), "depth": depth},
        )
        await conn.session.execute(
            text("""
            INSERT INTO checkpoint_blobs(thread_id, checkpoint_ns, channel, version, type, blob)
            SELECT :tid, '', 'payload', i::text, 'bytes',
                decode(repeat(md5(i::text), :copies),'hex')
            FROM generate_series(1,:depth) i
        """),
            {"tid": str(tid), "depth": depth, "copies": payload // 16},
        )
        await conn.session.execute(
            text("""
            INSERT INTO checkpoint_writes(thread_id, checkpoint_ns, checkpoint_id,
                task_id, idx, channel, type, blob)
            SELECT :tid, '', lpad(i::text,10,'0'), 'task', 0, 'payload', 'bytes',
                decode(repeat(md5(i::text), :copies),'hex')
            FROM generate_series(1,:depth) i
        """),
            {"tid": str(tid), "depth": depth, "copies": payload // 16},
        )
    elapsed, wal, boundary = [], [], []
    for _ in range(repeats):
        async with connect() as conn:
            await conn.session.execute(
                delete(RunCheckpointBaselineRow).where(RunCheckpointBaselineRow.run_id == rid)
            )
        started = time.perf_counter()
        async with connect() as conn:
            start_lsn = await conn.session.scalar(text("SELECT pg_current_wal_insert_lsn()::text"))
            run = await conn.session.get(RunRow, rid, with_for_update=True)
            thread = await conn.session.get(ThreadRow, tid, with_for_update=True)
            await capture_baseline(conn.session, run, thread)
            boundary.append(
                await conn.session.scalar(
                    text("""
                SELECT octet_length(to_jsonb(b)::text) FROM run_checkpoint_baselines b
                WHERE run_id=:rid
            """),
                    {"rid": rid},
                )
            )
        elapsed.append((time.perf_counter() - started) * 1000)
        async with connect() as conn:
            wal.append(
                int(
                    await conn.session.scalar(
                        text(
                            "SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(), CAST(CAST(:lsn AS text) AS pg_lsn))"
                        ),
                        {"lsn": start_lsn},
                    )
                )
            )
    async with connect() as conn:
        before = await table_bytes(conn, tid)
        thread = await conn.session.get(ThreadRow, tid, with_for_update=True)
        started = time.perf_counter()
        await prune_checkpoints(conn.session, thread)
        prune_ms = (time.perf_counter() - started) * 1000
        after = await table_bytes(conn, tid)
    return {
        "history": depth,
        "payload_bytes": payload,
        "repeats": repeats,
        "capture_ms": elapsed,
        "p50_ms": statistics.median(elapsed),
        "max_ms": max(elapsed),
        "p95_ms": sorted(elapsed)[math.ceil(len(elapsed) * 0.95) - 1],
        "boundary_logical_bytes": boundary,
        "capture_wal_bytes": wal,
        "tables_before": before,
        "tables_after_prune": after,
        "prune_ms": prune_ms,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
        "lock_wait": "uncontended; concurrency measured by dedicated safety tests",
    }


async def table_bytes(conn, tid) -> dict:
    return {
        table: dict(
            (
                await conn.session.execute(
                    text(f"""
        SELECT count(*) AS rows, COALESCE(sum(octet_length(to_jsonb(t)::text)),0)::bigint
            AS logical_bytes FROM {table} t WHERE thread_id=:tid
    """),
                    {"tid": str(tid)},
                )
            )
            .mappings()
            .one()
        )
        for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs")
    }


async def main(args) -> None:
    await start_pool()
    try:
        result = await measure(args.depth, args.payload, args.repeats)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))  # noqa: T201 - benchmark output
    finally:
        await stop_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--depth", type=int, choices=[100, 1000, 10000], required=True)
    parser.add_argument("--payload", type=int, choices=[1024, 16384], required=True)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
