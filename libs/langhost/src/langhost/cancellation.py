"""Thread-local atomic cancellation and durable execution acknowledgements."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request
from starlette.responses import JSONResponse

from langgraph_runtime_pg.auth import principal_from_scope, scoped_idempotency_key
from langgraph_runtime_pg.authorization import metadata_predicate
from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.models import RunCancellationRow, RunRow, RuntimeEventRow, ThreadRow


def _identity(request: Request) -> str:
    principal = principal_from_scope(request.scope)
    return principal.subject if principal else "anonymous"


async def _receipt(session, row, *, offset=0, limit=100):
    ids = list(
        {UUID(target["run_id"]) for target in row.targets}
        | {UUID(run) for run in row.reconcile_run_ids}
    )
    stopped = set(
        await session.scalars(
            select(RunRow.run_id)
            .join(RuntimeEventRow, RuntimeEventRow.run_id == RunRow.run_id)
            .where(
                RunRow.run_id.in_(ids),
                RunRow.status.in_(["success", "error", "interrupted", "timeout"]),
                RunRow.lease_owner.is_(None),
                RuntimeEventRow.terminal.is_(True),
                RuntimeEventRow.payload["execution_stopped"].as_boolean().is_not(False),
                or_(
                    RunRow.reason.is_(None),
                    RunRow.reason.not_in(["cancel_requested", "rollback"]),
                    RuntimeEventRow.payload["execution_stopped"].as_boolean().is_(True),
                ),
            )
        )
    )
    live = set(
        await session.scalars(
            select(RunRow.run_id).where(
                RunRow.run_id.in_(ids),
                or_(RunRow.lease_owner.is_not(None), RunRow.status.in_(["pending", "running"])),
            )
        )
    )
    targets, confirmed = [], 0
    for index, target in enumerate(row.targets):
        confirmed_target = UUID(target["run_id"]) in stopped
        confirmed += confirmed_target
        if offset <= index < offset + limit:
            targets.append({**target, "execution_stopped": confirmed_target})
    return {
        "cancellation_id": str(row.cancellation_id),
        "thread_id": str(row.thread_id),
        "accepted_at": row.accepted_at.isoformat(),
        "target_count": len(row.targets),
        "pending_cancelled_count": sum(t["was_pending"] for t in row.targets),
        "execution_stopped": len(stopped) == len(ids),
        "confirmation_unavailable": bool(set(ids) - stopped - live),
        "reconcile_run_ids": row.reconcile_run_ids,
        "reconcile_targets": [
            {"run_id": str(run.run_id), "status": run.status, "reason": run.reason}
            for run in await session.scalars(
                select(RunRow).where(
                    RunRow.run_id.in_([UUID(run) for run in row.reconcile_run_ids])
                )
            )
        ],
        "has_pending_interrupts": row.has_pending_interrupts,
        "targets": targets,
        "next_offset": offset + limit if offset + limit < len(row.targets) else None,
    }


async def cancel_active(request: Request) -> JSONResponse:
    from langhost.core_api import _authorize, _cancel_row

    try:
        payload = await request.json()
        if (
            not isinstance(payload, dict)
            or set(payload) - {"cancellation_id", "action", "reconcile_run_ids"}
            or payload["action"] != "interrupt"
        ):
            raise ValueError()
        cancellation_id = UUID(payload["cancellation_id"])
        reconcile_ids = payload.get("reconcile_run_ids", [])
        if not isinstance(reconcile_ids, list) or len(reconcile_ids) > 100:
            raise ValueError()
        reconcile_ids = sorted({str(UUID(run)) for run in reconcile_ids})
        thread_id = UUID(request.path_params["thread_id"])
        if request.query_params:
            raise ValueError()
        raw_key = request.headers.get("Idempotency-Key", "")
        if not 1 <= len(raw_key) <= 128:
            raise ValueError()
    except (TypeError, ValueError, KeyError):
        return JSONResponse({"detail": "invalid cancellation request"}, 422)
    filters = await _authorize(
        request,
        "threads",
        "update",
        {"thread_id": thread_id, "cancel_active": True, "cancellation_id": cancellation_id},
    )
    principal = _identity(request)
    key = scoped_idempotency_key(principal_from_scope(request.scope), raw_key)
    try:
        async with connect() as conn:
            thread = await conn.session.scalar(
                select(ThreadRow)
                .where(
                    ThreadRow.thread_id == thread_id,
                    metadata_predicate(ThreadRow.metadata_, filters),
                )
                .with_for_update(nowait=True)
            )
            if thread is None:
                return JSONResponse({"detail": "thread not found"}, 404)
            existing = await conn.session.scalar(
                select(RunCancellationRow).where(
                    RunCancellationRow.thread_id == thread_id,
                    RunCancellationRow.principal == principal,
                    RunCancellationRow.idempotency_key == key,
                )
            )
            if existing is not None:
                if (
                    existing.cancellation_id != cancellation_id
                    or existing.reconcile_run_ids != reconcile_ids
                ):
                    return JSONResponse({"detail": "idempotency conflict"}, 409)
                return JSONResponse(
                    await _receipt(conn.session, existing),
                    202,
                    headers={"Cache-Control": "no-store"},
                )
            if await conn.session.get(RunCancellationRow, cancellation_id) is not None:
                return JSONResponse({"detail": "cancellation ID conflict"}, 409)
            if reconcile_ids:
                owned = set(
                    await conn.session.scalars(
                        select(RunRow.run_id).where(
                            RunRow.thread_id == thread_id,
                            RunRow.run_id.in_([UUID(run) for run in reconcile_ids]),
                        )
                    )
                )
                if owned != {UUID(run) for run in reconcile_ids}:
                    return JSONResponse({"detail": "reconciliation target not found"}, 404)
            rows = list(
                await conn.session.scalars(
                    select(RunRow)
                    .where(
                        RunRow.thread_id == thread_id,
                        or_(
                            RunRow.status.in_(("pending", "running")),
                            and_(
                                RunRow.reason == "cancel_requested", RunRow.lease_owner.is_not(None)
                            ),
                        ),
                    )
                    .order_by(RunRow.run_id)
                    .with_for_update(nowait=True)
                )
            )
            row = RunCancellationRow(
                cancellation_id=cancellation_id,
                thread_id=thread_id,
                principal=principal,
                idempotency_key=key,
                accepted_at=datetime.now(UTC),
                targets=[
                    {"run_id": str(r.run_id), "was_pending": r.status == "pending"} for r in rows
                ],
                reconcile_run_ids=reconcile_ids,
                has_pending_interrupts=bool(thread.interrupts),
            )
            conn.session.add(row)
            # ponytail: per-Run cancel/event writes; batch them if large-queue latency matters.
            for run in rows:
                await _cancel_row(request, conn, run, "interrupt")
            await conn.session.flush()
            result = await _receipt(conn.session, row)
        return JSONResponse(result, 202, headers={"Cache-Control": "no-store"})
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            return JSONResponse({"detail": "cancellation boundary busy"}, 409)
        raise


async def get_cancellation(request: Request) -> JSONResponse:
    from langhost.core_api import _authorize

    try:
        thread_id = UUID(request.path_params["thread_id"])
        cancellation_id = UUID(request.path_params["cancellation_id"])
        if set(request.query_params) - {"offset", "limit"}:
            raise ValueError()
        offset = int(request.query_params.get("offset", "0"))
        limit = int(request.query_params.get("limit", "100"))
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError()
    except ValueError:
        return JSONResponse({"detail": "invalid cancellation query"}, 422)
    # The callback can grant this receipt alone after ACL revocation. This
    # server-created marker never authorizes Run/state or a new cancellation.
    await _authorize(
        request,
        "threads",
        "read",
        {
            "thread_id": thread_id,
            "cancellation_id": cancellation_id,
            "cancellation_receipt": True,
        },
    )
    async with connect() as conn:
        row = await conn.session.get(RunCancellationRow, cancellation_id)
        if row is None or row.thread_id != thread_id or row.principal != _identity(request):
            return JSONResponse({"detail": "cancellation not found"}, 404)
        return JSONResponse(
            await _receipt(conn.session, row, offset=offset, limit=limit),
            headers={"Cache-Control": "no-store"},
        )
