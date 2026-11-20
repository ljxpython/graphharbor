"""Byte-bound acceptance facts; Redis and execution are not acceptance evidence."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from starlette.exceptions import HTTPException

from langgraph_runtime_pg.authorization import metadata_predicate
from langgraph_runtime_pg.models import RunAcceptanceRow, RunRow
from langgraph_runtime_pg.run_state import is_terminal

HEX = re.compile(r"[0-9a-f]{64}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def request_digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def receipt_key(scope_id: str, credential_id: str, key_sha256: str) -> str:
    return hashlib.sha256(
        f"graphharbor:run-acceptance:v1\0{scope_id}\0{credential_id}\0{key_sha256}".encode("ascii")
    ).hexdigest()


@dataclass(frozen=True)
class AcceptanceGrant:
    scope_id: str
    credential_id: str
    operation: str
    thread_id: UUID
    key_sha256: str
    request_digest: str

    @classmethod
    def parse(cls, value: Any) -> AcceptanceGrant:
        names = {
            "scope_id",
            "credential_id",
            "operation",
            "thread_id",
            "key_sha256",
            "request_digest",
        }
        if not isinstance(value, dict) or set(value) != names:
            raise ValueError("run acceptance requires a complete authenticated grant")
        for name in ("scope_id", "credential_id", "key_sha256"):
            if not isinstance(value[name], str) or not HEX.fullmatch(value[name]):
                raise ValueError("invalid run acceptance binding")
        if value["operation"] not in ("create", "read"):
            raise ValueError("invalid run acceptance operation")
        if not isinstance(value["request_digest"], str) or not DIGEST.fullmatch(
            value["request_digest"]
        ):
            raise ValueError("invalid run acceptance digest")
        try:
            thread = UUID(value["thread_id"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("invalid run acceptance thread") from exc
        if str(thread) != value["thread_id"]:
            raise ValueError("run acceptance thread must be canonical")
        return cls(**{**value, "thread_id": thread})

    @property
    def receipt_key(self) -> str:
        return receipt_key(self.scope_id, self.credential_id, self.key_sha256)

    def authorization_value(self) -> dict[str, Any]:
        return {
            "run_acceptance_receipt": True,
            "thread_id": self.thread_id,
            "key_sha256": self.key_sha256,
            "request_digest": self.request_digest,
        }


def retention_seconds() -> int:
    value = int(os.environ.get("GRAPHHARBOR_RUN_ACCEPTANCE_RETENTION_SECONDS", "604800"))
    if value < 1:
        raise ValueError("GRAPHHARBOR_RUN_ACCEPTANCE_RETENTION_SECONDS must be a positive integer")
    return value


class RunAcceptanceRepository:
    async def acquire_key(self, session: Any, grant: AcceptanceGrant) -> None:
        lock = int.from_bytes(bytes.fromhex(grant.receipt_key)[:8], "big", signed=True)
        await session.execute(select(func.pg_advisory_xact_lock(lock)))

    async def get(self, session: Any, grant: AcceptanceGrant) -> RunAcceptanceRow | None:
        return await session.get(RunAcceptanceRow, grant.receipt_key)

    async def response(
        self,
        session: Any,
        grant: AcceptanceGrant,
        row: RunAcceptanceRow | None,
        filters: dict[str, Any],
        *,
        replay: bool = False,
    ) -> dict[str, Any]:
        now = await session.scalar(select(func.clock_timestamp()))
        run = None
        if row is not None and row.run_id is not None:
            run = (
                await session.execute(
                    select(RunRow.status, RunRow.lease_owner).where(
                        RunRow.run_id == row.run_id, RunRow.thread_id == row.thread_id
                    )
                )
            ).first()
        active = run is not None and (not is_terminal(run.status) or run.lease_owner is not None)
        expired = row is not None and (
            row.outcome == "expired"
            or (row.details_expires_at is not None and row.details_expires_at <= now and not active)
        )
        if row is not None and not expired:
            allowed = await session.scalar(
                select(RunAcceptanceRow.receipt_key).where(
                    RunAcceptanceRow.receipt_key == row.receipt_key,
                    metadata_predicate(RunAcceptanceRow.authorization_metadata, filters),
                )
            )
            if allowed is None:
                raise HTTPException(404, "acceptance receipt not found")
        if row is not None and (
            row.thread_id != grant.thread_id or row.request_digest != grant.request_digest
        ):
            raise HTTPException(409, "acceptance key is bound to another request")
        if expired and replay:
            raise HTTPException(409, "acceptance details expired; key remains closed")
        result = (
            "unknown"
            if row is None or expired
            else ("accepted" if row.outcome == "accepted" else "definitively_not_accepted")
        )
        details = row is not None and not expired
        return {
            "schema_version": 1,
            "result": result,
            "thread_id": str(grant.thread_id),
            "key_sha256": grant.key_sha256,
            "request_digest": grant.request_digest,
            "run_id": str(row.run_id) if details and row is not None and row.run_id else None,
            "accepted_at": row.accepted_at.isoformat()
            if details and row is not None and row.accepted_at
            else None,
            "observed_at": now.isoformat(),
            "details_expires_at": row.details_expires_at.isoformat()
            if details and row is not None and row.details_expires_at
            else None,
            "run_state": {
                "available": bool(details and run),
                "status": run.status if details and run else None,
            },
            "reason": "receipt_expired"
            if expired
            else ("no_receipt" if row is None else row.rejection_reason),
        }

    async def record(
        self,
        session: Any,
        grant: AcceptanceGrant,
        metadata: dict,
        run: RunRow | None = None,
    ) -> RunAcceptanceRow:
        now = await session.scalar(select(func.clock_timestamp()))
        row = RunAcceptanceRow(
            receipt_key=grant.receipt_key,
            scope_id=grant.scope_id,
            credential_id=grant.credential_id,
            key_sha256=grant.key_sha256,
            thread_id=grant.thread_id,
            request_digest=grant.request_digest,
            outcome="accepted" if run else "rejected",
            run_id=run.run_id if run else None,
            accepted_at=now if run else None,
            rejection_reason=None if run else "multitask_rejected",
            authorization_metadata=dict(metadata),
            recorded_at=now,
            details_expires_at=None if run else now + timedelta(seconds=retention_seconds()),
        )
        session.add(row)
        await session.flush()
        return row

    async def maintain_acceptance_receipts(self, session: Any, *, batch_size: int = 1000) -> int:
        if not 1 <= batch_size <= 1000:
            raise ValueError("receipt maintenance batch must be 1..1000")
        now = await session.scalar(select(func.clock_timestamp()))
        seconds = retention_seconds()
        active = exists(
            select(RunRow.run_id).where(
                RunRow.run_id == RunAcceptanceRow.run_id,
                or_(RunRow.status.in_(("pending", "running")), RunRow.lease_owner.is_not(None)),
            )
        )
        close = (RunAcceptanceRow.outcome == "accepted") & RunAcceptanceRow.closed_observed_at.is_(
            None
        )
        expire = (RunAcceptanceRow.outcome != "expired") & (
            RunAcceptanceRow.details_expires_at <= now
        )
        candidates: list[RunAcceptanceRow] = []
        for predicate, order in (
            (expire, RunAcceptanceRow.details_expires_at),
            (close, RunAcceptanceRow.recorded_at),
        ):
            remaining = batch_size - len(candidates)
            if not remaining:
                break
            candidates.extend(
                (
                    await session.scalars(
                        select(RunAcceptanceRow)
                        .where(predicate, ~active)
                        .order_by(order)
                        .limit(remaining)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
        changed = 0
        for row in candidates:
            if row.run_id is not None:
                # Requeue and expiry serialize on the Run; never lock a Thread here.
                run = await session.scalar(
                    select(RunRow)
                    .where(RunRow.run_id == row.run_id)
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
                if run is None:
                    if await session.scalar(
                        select(RunRow.run_id).where(RunRow.run_id == row.run_id)
                    ):
                        continue
                elif not is_terminal(run.status) or run.lease_owner is not None:
                    continue
            if row.outcome == "accepted" and row.closed_observed_at is None:
                row.closed_observed_at = now
                row.details_expires_at = now + timedelta(seconds=seconds)
            elif row.details_expires_at is not None and row.details_expires_at <= now:
                row.outcome = "expired"
                row.run_id = row.accepted_at = row.rejection_reason = row.authorization_metadata = (
                    None
                )
            changed += 1
        await session.flush()
        return changed
