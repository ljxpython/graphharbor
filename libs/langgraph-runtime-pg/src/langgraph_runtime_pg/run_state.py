"""Deterministic run state machine shared by API and workers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from langgraph_runtime_pg.protocol import (
    PUBLIC_RUN_STATUSES,
    RunReason,
    RunStatus,
    ensure_status,
)

MAX_INFRASTRUCTURE_RETRIES: Final = 3


class InvalidTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Transition:
    status: RunStatus
    reason: RunReason
    retry_count: int


_ALLOWED: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset(
        {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.ERROR, RunStatus.INTERRUPTED}
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.PENDING,
            RunStatus.RUNNING,
            RunStatus.SUCCESS,
            RunStatus.ERROR,
            RunStatus.TIMEOUT,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.INTERRUPTED: frozenset({RunStatus.INTERRUPTED, RunStatus.PENDING}),
    RunStatus.SUCCESS: frozenset({RunStatus.SUCCESS}),
    RunStatus.ERROR: frozenset({RunStatus.ERROR}),
    RunStatus.TIMEOUT: frozenset({RunStatus.TIMEOUT}),
}


def is_terminal(status: str | RunStatus) -> bool:
    return ensure_status(status) in {
        RunStatus.SUCCESS,
        RunStatus.ERROR,
        RunStatus.TIMEOUT,
        RunStatus.INTERRUPTED,
    }


def transition(
    current: str | RunStatus,
    target: str | RunStatus,
    *,
    reason: str | RunReason,
    retry_count: int = 0,
) -> Transition:
    """Validate one state change and normalize the public status/reason pair."""
    current_status = ensure_status(current)
    target_status = ensure_status(target)
    try:
        transition_allowed = target_status in _ALLOWED[current_status]
    except KeyError as exc:  # pragma: no cover - ensure_status makes this unreachable
        raise InvalidTransition(f"Unknown current status: {current!r}") from exc
    if not transition_allowed:
        raise InvalidTransition(f"Cannot transition run from {current_status} to {target_status}")

    normalized_reason = reason if isinstance(reason, RunReason) else RunReason(reason)
    if retry_count < 0 or retry_count > MAX_INFRASTRUCTURE_RETRIES:
        raise InvalidTransition(
            f"retry_count must be between 0 and {MAX_INFRASTRUCTURE_RETRIES}, got {retry_count}"
        )
    if (
        normalized_reason is RunReason.INFRASTRUCTURE_ERROR
        and retry_count > MAX_INFRASTRUCTURE_RETRIES
    ):
        raise InvalidTransition("infrastructure retries are capped at three")
    if target_status is RunStatus.INTERRUPTED and normalized_reason not in {
        RunReason.CANCEL_REQUESTED,
        RunReason.HITL_INTERRUPT,
        RunReason.SHUTDOWN_REQUEUE,
        RunReason.ROLLBACK,
    }:
        raise InvalidTransition(
            "interrupted runs require a cancellation, HITL, shutdown, or rollback reason"
        )
    if target_status is RunStatus.SUCCESS and normalized_reason is not RunReason.COMPLETED:
        raise InvalidTransition("success runs require reason='completed'")
    return Transition(target_status, normalized_reason, retry_count)


def validate_persisted_status(value: str) -> str:
    """Validate a database value at the trust boundary."""
    if value not in PUBLIC_RUN_STATUSES:
        raise InvalidTransition(f"Invalid persisted run status: {value!r}")
    return value
