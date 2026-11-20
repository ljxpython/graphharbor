"""Self-owned production runtime lifecycle.

This module deliberately imports no ``langgraph_api`` private module.  The old
``lifespan`` module remains available for the compatibility spike only.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import structlog

from langgraph_runtime_pg.database import healthcheck, schema_ready, start_pool, stop_pool
from langgraph_runtime_pg.redis_stream import stream_ready

logger = structlog.stdlib.get_logger(__name__)


def configure_structured_logging() -> None:
    """Emit GraphHarbor runtime events as JSON for self-hosted collectors."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    )


@dataclass(slots=True)
class RuntimeReadiness:
    ready: bool = False
    reason: str | None = "not started"
    checks: dict[str, bool] | None = None


@asynccontextmanager
async def lifespan(
    app: Any | None = None,
    *,
    readiness: RuntimeReadiness | None = None,
    **_: Any,
) -> AsyncIterator[None]:
    """Start durable dependencies, then close them in reverse order."""
    configure_structured_logging()
    state = readiness or RuntimeReadiness()
    state.checks = {
        **(state.checks or {}),
        "postgres": False,
        "schema": False,
        "redis": False,
        "queue": False,
    }
    # Production migration is an explicit pre-deploy command, never a startup side effect.
    previous_auto_migrate = os.environ.get("LG_RUNTIME_PG_AUTO_MIGRATE")
    cron_task: asyncio.Task[None] | None = None
    webhook_task: asyncio.Task[None] | None = None
    os.environ["LG_RUNTIME_PG_AUTO_MIGRATE"] = "false"
    try:
        await start_pool()
        await healthcheck(check_db=True)
        state.checks["postgres"] = True
        state.checks["schema"] = await schema_ready()
        if not state.checks["schema"]:
            raise RuntimeError(
                "PostgreSQL schema contract is missing; run graphharbor migrate upgrade"
            )
        state.checks["redis"] = stream_ready()
        state.checks["queue"] = state.checks["redis"]
        state.ready = True
        state.reason = None
        from langgraph_runtime_pg.cron import cron_scheduler

        cron_task = asyncio.create_task(cron_scheduler(), name="graphharbor-cron")
        if (
            bool(getattr(getattr(app, "state", None), "webhook_dispatcher_enabled", False))
            or os.environ.get("GRAPHHARBOR_WEBHOOK_DISPATCHER") == "1"
        ):
            from langgraph_runtime_pg.webhook_delivery import delivery_loop

            webhook_task = asyncio.create_task(delivery_loop(), name="graphharbor-webhooks")
        logger.info("GraphHarbor production runtime ready")
        yield
    except Exception as exc:
        state.ready = False
        state.reason = str(exc)
        logger.exception("GraphHarbor production runtime failed to start")
        raise
    finally:
        for task in (cron_task, webhook_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        state.ready = False
        state.reason = "stopped"
        if state.checks is not None:
            state.checks.update(
                {"postgres": False, "schema": False, "redis": False, "queue": False}
            )
        await stop_pool()
        if previous_auto_migrate is None:
            os.environ.pop("LG_RUNTIME_PG_AUTO_MIGRATE", None)
        else:
            os.environ["LG_RUNTIME_PG_AUTO_MIGRATE"] = previous_auto_migrate
