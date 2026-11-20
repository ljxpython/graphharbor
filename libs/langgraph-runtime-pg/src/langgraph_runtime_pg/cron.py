"""Durable cron dispatch for the self-owned GraphHarbor runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import croniter
import structlog
from sqlalchemy import or_, select

from langgraph_runtime_pg.auth import sign_runtime_context
from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.models import AssistantRow, CronRow, ThreadRow
from langgraph_runtime_pg.redis_stream import enqueue_run
from langgraph_runtime_pg.run_store import RunRepository

logger = structlog.stdlib.get_logger(__name__)


def next_cron_date(schedule: str, base_time: datetime, timezone: str | None = None) -> datetime:
    local_time = base_time.astimezone(ZoneInfo(timezone)) if timezone else base_time
    return croniter.croniter(schedule, local_time).get_next(datetime).astimezone(UTC)


async def dispatch_due_crons() -> int:
    """Claim due schedules and commit each resulting Run with its next trigger."""
    now = datetime.now(UTC)
    async with connect() as conn:
        rows = (
            (
                await conn.session.execute(
                    select(CronRow)
                    .where(
                        CronRow.enabled.is_(True),
                        or_(CronRow.end_time.is_(None), CronRow.end_time >= now),
                        or_(CronRow.next_run_date.is_(None), CronRow.next_run_date <= now),
                    )
                    .order_by(CronRow.next_run_date.asc().nullsfirst())
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        dispatched = 0
        for cron in rows:
            try:
                async with conn.session.begin_nested():
                    scheduled_for = cron.next_run_date or now
                    payload = dict(cron.payload or {})
                    trusted_context = payload.pop("__graphharbor_runtime_context", None)
                    config = dict(payload.get("config") or {})
                    configurable = dict(config.get("configurable") or {})
                    configurable["cron_id"] = str(cron.cron_id)
                    config["configurable"] = configurable
                    payload["config"] = config
                    metadata = {
                        **(cron.metadata_ or {}),
                        **(payload.get("metadata") or {}),
                        "cron_id": str(cron.cron_id),
                    }
                    payload["metadata"] = metadata
                    if cron.on_run_completed == "keep":
                        payload.setdefault("on_completion", "keep")
                    thread_id = cron.thread_id
                    if thread_id is None and cron.on_run_completed == "keep":
                        assistant = await conn.session.get(AssistantRow, cron.assistant_id)
                        thread = ThreadRow(
                            graph_id=assistant.graph_id if assistant else None,
                            metadata_=metadata,
                            config={},
                            interrupts={},
                        )
                        conn.session.add(thread)
                        await conn.session.flush()
                        thread_id = thread.thread_id
                    run = await RunRepository().create(
                        conn.session,
                        assistant_id=cron.assistant_id,
                        thread_id=thread_id,
                        kwargs=payload,
                        metadata=metadata,
                        idempotency_key=f"cron:{cron.cron_id}:{scheduled_for.isoformat()}",
                        multitask_strategy=str(payload.get("multitask_strategy") or "enqueue"),
                    )
                    if trusted_context is not None:
                        run.kwargs = {
                            **run.kwargs,
                            "runtime_context_token": sign_runtime_context(
                                trusted_context,
                                run_id=str(run.run_id),
                                thread_id=str(thread_id) if thread_id else None,
                            ),
                        }
                    try:
                        cron.next_run_date = next_cron_date(cron.schedule, now, cron.timezone)
                    except croniter.CroniterBadDateError:
                        # A finite expression can be exhausted after this successful Run.
                        cron.next_run_date = None
                        cron.enabled = False
                    cron.updated_at = now
                conn.schedule_after_commit(lambda run_id=run.run_id: enqueue_run(run_id))
                dispatched += 1
            except Exception:
                logger.exception("Cron dispatch failed", cron_id=str(cron.cron_id))
        return dispatched


async def cron_scheduler() -> None:
    while True:
        try:
            await dispatch_due_crons()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Cron dispatch failed; next poll will retry")
        await asyncio.sleep(1)
