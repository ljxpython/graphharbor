"""Exercise duplicate queue hints and concurrent terminal transitions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4


async def _run_case() -> dict[str, object]:
    from sqlalchemy import func, select

    from langgraph_runtime_pg.database import connect, get_session_factory
    from langgraph_runtime_pg.models import (
        AssistantRow,
        RunLeaseRow,
        RunRow,
        RuntimeEventRow,
        ThreadRow,
    )
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.redis_stream import dequeue_run_hint, enqueue_run
    from langgraph_runtime_pg.run_store import RunOwnershipError, RunRepository
    from langhost.core_api import _cancel_row

    assistant_ids: list[UUID] = []
    thread_ids: list[UUID] = []
    run_ids: list[UUID] = []

    async def create_run(*, threaded: bool) -> RunRow:
        assistant_id = uuid4()
        thread_id = uuid4() if threaded else None
        assistant_ids.append(assistant_id)
        if thread_id is not None:
            thread_ids.append(thread_id)
        async with connect() as conn:
            conn.session.add(
                AssistantRow(
                    assistant_id=assistant_id,
                    graph_id="terminal-idempotency",
                    name="terminal-idempotency",
                    config={},
                    context={},
                    metadata_={},
                )
            )
            if thread_id is not None:
                conn.session.add(
                    ThreadRow(
                        thread_id=thread_id,
                        status="idle",
                        metadata_={},
                        config={},
                        interrupts={},
                    )
                )
            run = await RunRepository().create(
                conn.session,
                assistant_id=assistant_id,
                thread_id=thread_id,
                kwargs={"input": {}},
                metadata={},
                tenant_id="acceptance-terminal",
                project_id="acceptance-terminal",
            )
            run_ids.append(run.run_id)
            return run

    async def cleanup() -> None:
        async with connect() as conn:
            for run_id in run_ids:
                row = await conn.session.get(RunRow, run_id)
                if row is not None:
                    await conn.session.delete(row)
            for thread_id in thread_ids:
                row = await conn.session.get(ThreadRow, thread_id)
                if row is not None:
                    await conn.session.delete(row)
            for assistant_id in assistant_ids:
                row = await conn.session.get(AssistantRow, assistant_id)
                if row is not None:
                    await conn.session.delete(row)

    queue_run = await create_run(threaded=False)
    await enqueue_run(queue_run.run_id)
    await enqueue_run(queue_run.run_id)
    hints = [await dequeue_run_hint(), await dequeue_run_hint()]
    async with connect() as conn:
        repository = RunRepository()
        first_claim = await repository.claim_next(conn.session, "queue-worker")
        second_claim = await repository.claim_next(conn.session, "queue-worker-2")
    if first_claim is None or first_claim.run_id != queue_run.run_id or second_claim is not None:
        raise AssertionError("duplicate queue hints caused more than one claim")

    race_run = await create_run(threaded=True)
    async with connect() as conn:
        assert await RunRepository().claim_next(conn.session, "race-worker") is not None
        row = await conn.session.get(RunRow, race_run.run_id)
        assert row is not None
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        lease = await conn.session.get(RunLeaseRow, race_run.run_id)
        assert lease is not None
        lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    async def reap() -> int:
        async with get_session_factory()() as session, session.begin():
            return await RunRepository().requeue_expired(
                session, now=datetime.now(UTC) + timedelta(seconds=1)
            )

    async def late_finalize() -> str:
        try:
            async with get_session_factory()() as session, session.begin():
                finished = await RunRepository().finish(
                    session,
                    race_run.run_id,
                    "race-worker",
                    RunStatus.SUCCESS,
                    reason=RunReason.COMPLETED,
                )
                return "noop" if finished is None else "success"
        except RunOwnershipError:
            return "lost_lease"

    async def cancel() -> str:
        async with connect() as conn:
            row = await conn.session.get(RunRow, race_run.run_id)
            assert row is not None
            await _cancel_row(None, conn, row, "interrupt")
            return "completed"

    reaper_count, finalize_result, cancel_result = await asyncio.gather(
        reap(), late_finalize(), cancel()
    )
    async with connect() as conn:
        row = await conn.session.get(RunRow, race_run.run_id)
        terminal_count = await conn.session.scalar(
            select(func.count())
            .select_from(RuntimeEventRow)
            .where(RuntimeEventRow.run_id == race_run.run_id, RuntimeEventRow.terminal.is_(True))
        )
        lease = await conn.session.get(RunLeaseRow, race_run.run_id)
        assert row is not None
        evidence = {
            "queue_hints": [hint.decode() if isinstance(hint, bytes) else hint for hint in hints],
            "single_claim": first_claim.run_id == queue_run.run_id and second_claim is None,
            "reaper_count": reaper_count,
            "late_finalize": finalize_result,
            "cancel": cancel_result,
            "final_status": row.status,
            "terminal_event_count": int(terminal_count or 0),
            "lease_present": lease is not None,
        }
        if (
            row.status not in {RunStatus.SUCCESS.value, RunStatus.INTERRUPTED.value}
            or evidence["terminal_event_count"] != 1
            or evidence["lease_present"]
        ):
            raise AssertionError(f"terminal transition invariant failed: {evidence}")
    await cleanup()
    return evidence


async def _main() -> dict[str, object]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-uri", default=os.environ.get("DATABASE_URI"))
    parser.add_argument("--redis-uri", default=os.environ.get("REDIS_URI"))
    args = parser.parse_args()
    if not args.database_uri or not args.redis_uri:
        raise RuntimeError("DATABASE_URI and REDIS_URI are required")
    os.environ["DATABASE_URI"] = args.database_uri
    os.environ["REDIS_URI"] = args.redis_uri
    os.environ["GRAPHHARBOR_REDIS_PREFIX"] = f"graphharbor:acceptance:terminal:{uuid4().hex}"

    from langgraph_runtime_pg.database import start_pool, stop_pool

    await start_pool()
    try:
        return await _run_case()
    finally:
        await stop_pool()


def main() -> int:
    try:
        sys.stdout.write(json.dumps({"status": "passed", "evidence": asyncio.run(_main())}) + "\n")
    except Exception as exc:
        sys.stdout.write(
            json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}) + "\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
