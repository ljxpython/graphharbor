"""Atomic JSONB merge and optional top-level compare-and-set for Threads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB
from starlette.exceptions import HTTPException

from langgraph_runtime_pg.authorization import metadata_predicate
from langgraph_runtime_pg.models import ThreadRow


async def patch_thread_metadata(
    session: Any,
    thread_id: UUID,
    metadata: dict,
    *,
    if_metadata: dict | None = None,
    filters: dict | None = None,
) -> ThreadRow | None:
    if not isinstance(metadata, dict) or (
        if_metadata is not None
        and (
            not isinstance(if_metadata, dict)
            or not if_metadata
            or len(if_metadata) > 32
            or any(not isinstance(key, str) or not key or len(key) > 256 for key in if_metadata)
        )
    ):
        raise HTTPException(422, "Invalid metadata condition")
    visible = (
        ThreadRow.thread_id == thread_id,
        metadata_predicate(ThreadRow.metadata_, filters or {}),
    )
    conditions = [
        func.coalesce(ThreadRow.metadata_[key], type_coerce(None, JSONB))
        == type_coerce(value, JSONB)
        for key, value in (if_metadata or {}).items()
    ]
    row = await session.scalar(
        update(ThreadRow)
        .where(*visible, *conditions, ThreadRow.deletion_requested_at.is_(None))
        .values(
            metadata_=ThreadRow.metadata_.op("||")(type_coerce(metadata, JSONB)),
            updated_at=datetime.now(UTC),
        )
        .returning(ThreadRow)
        .execution_options(synchronize_session=False, populate_existing=True)
    )
    if (
        row is None
        and await session.scalar(
            select(ThreadRow.thread_id).where(
                *visible, ThreadRow.deletion_requested_at.is_not(None)
            )
        )
        is not None
    ):
        raise HTTPException(409, "thread deletion has been requested")
    if (
        row is None
        and if_metadata is not None
        and await session.scalar(select(ThreadRow.thread_id).where(*visible)) is not None
    ):
        raise HTTPException(409, "metadata condition failed")
    return row
