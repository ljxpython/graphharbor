"""Serialize shared PostgreSQL schema setup across Runtime processes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

SETUP_ADVISORY_KEY = 716_203_117


async def run_schema_setup(
    uri: str,
    setup: Callable[[Any], Awaitable[None]],
) -> None:
    """Run setup while the same PostgreSQL connection owns the advisory lock."""

    async with await AsyncConnection.connect(
        uri,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    ) as connection:
        # A blocked SELECT can hold a snapshot that CREATE INDEX CONCURRENTLY needs to finish.
        while True:
            cursor = await connection.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired", (SETUP_ADVISORY_KEY,)
            )
            row = await cursor.fetchone()
            if row and row["acquired"]:
                break
            await asyncio.sleep(0.1)
        try:
            await setup(connection)
        finally:
            await connection.execute("SELECT pg_advisory_unlock(%s)", (SETUP_ADVISORY_KEY,))


__all__ = ["SETUP_ADVISORY_KEY", "run_schema_setup"]
