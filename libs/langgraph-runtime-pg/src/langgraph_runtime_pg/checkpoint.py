"""Checkpointer factory wrapping langgraph-checkpoint-postgres."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata, CheckpointTuple
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from langgraph_runtime_pg.checkpoint_mutations import CheckpointConflict, checkpoint_writer
from langgraph_runtime_pg.database import to_psycopg_uri
from langgraph_runtime_pg.schema_setup import run_schema_setup

_POOL: AsyncConnectionPool[Any] | None = None
_CHECKPOINTER: AsyncPostgresSaver | None = None
_SETUP_LOCK = asyncio.Lock()


class FencedPostgresSaver(AsyncPostgresSaver):
    """Serialize maintenance with writes and reject stale worker generations."""

    @asynccontextmanager
    async def _writer(self, config: RunnableConfig) -> AsyncIterator[AsyncPostgresSaver]:
        thread_id = str(config["configurable"]["thread_id"])
        writer = checkpoint_writer.get()
        async with (
            cast(
                AsyncConnectionPool[AsyncConnection[dict[str, Any]]], self.conn
            ).connection() as connection,
            connection.transaction(),
        ):
            if writer:
                run_id, owner, generation = writer
                cursor = await connection.execute(
                    "SELECT thread_id, status, lease_owner, retry_count, "
                    "lease_expires_at > now() AS valid FROM runs WHERE run_id=%s FOR UPDATE",
                    (run_id,),
                )
                run = await cursor.fetchone()
                if (
                    not run
                    or run["status"] != "running"
                    or not run["valid"]
                    or run["lease_owner"] != owner
                    or run["retry_count"] != generation
                    or str(run["thread_id"] or run_id) != thread_id
                ):
                    raise CheckpointConflict("checkpoint writer no longer owns the run")
            try:
                resource_id = UUID(thread_id)
            except ValueError:
                resource_id = None  # MCP/standalone graph checkpoint identifiers.
            if resource_id:
                await connection.execute(
                    "SELECT thread_id FROM threads WHERE thread_id=%s FOR UPDATE",
                    (resource_id,),
                )
                if not writer:
                    cursor = await connection.execute(
                        "SELECT 1 FROM runs WHERE thread_id=%s "
                        "AND (status='running' OR reason='rollback') LIMIT 1",
                        (resource_id,),
                    )
                    if await cursor.fetchone():
                        raise CheckpointConflict("thread has an active run or rollback")
                    # An explicit state update supersedes old rollback snapshots.
                    await connection.execute(
                        "DELETE FROM run_checkpoint_baselines WHERE thread_id=%s",
                        (resource_id,),
                    )
            yield AsyncPostgresSaver(connection, serde=self.serde)

    async def aput(self, config, checkpoint, metadata, new_versions):
        async with self._writer(config) as saver:
            writer = checkpoint_writer.get()
            if writer:
                metadata = {**metadata, "run_id": writer[0]}
            return await saver.aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        async with self._writer(config) as saver:
            await saver.aput_writes(config, writes, task_id, task_path)


async def setup_checkpointer() -> AsyncPostgresSaver:
    """Open a psycopg pool and initialize AsyncPostgresSaver tables."""
    global _POOL, _CHECKPOINTER
    async with _SETUP_LOCK:
        if _CHECKPOINTER is not None:
            return _CHECKPOINTER
        if _POOL is not None:
            try:
                await _POOL.close()
            except Exception:
                pass
            _POOL = None

        uri = to_psycopg_uri()

        # autocommit required: setup() runs CREATE INDEX CONCURRENTLY
        async def setup(connection: Any) -> None:
            await AsyncPostgresSaver(connection).setup()

        await run_schema_setup(uri, setup)
        pool = AsyncConnectionPool(
            conninfo=uri,
            min_size=1,
            max_size=10,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        try:
            await pool.open()
            saver = FencedPostgresSaver(cast(Any, pool))
        except Exception:
            try:
                await pool.close()
            except Exception:
                pass
            raise
        _POOL = pool
        _CHECKPOINTER = saver
        return _CHECKPOINTER


async def teardown_checkpointer() -> None:
    """Close the checkpointer pool."""
    global _POOL, _CHECKPOINTER
    _CHECKPOINTER = None
    pool = _POOL
    _POOL = None
    if pool is not None:
        try:
            await pool.close()
        except Exception:
            pass


async def reconnect_checkpointer() -> AsyncPostgresSaver:
    """Replace the saver pool after a PostgreSQL connection restart."""
    async with _SETUP_LOCK:
        global _POOL, _CHECKPOINTER
        _CHECKPOINTER = None
        pool = _POOL
        _POOL = None
        if pool is not None:
            try:
                await pool.close()
            except Exception:
                pass
    return await setup_checkpointer()


def Checkpointer(  # NOSONAR - upstream factory name is PascalCase
    *_args: Any, unpack_hook: Any = None, **_kwargs: Any
) -> AsyncPostgresSaver:
    """Return the process-wide AsyncPostgresSaver (requires setup_checkpointer).

    Name matches the upstream ``langgraph_runtime`` factory API (PascalCase).
    """
    del unpack_hook  # accepted for API parity with upstream; unused here
    if _CHECKPOINTER is None:
        # Test/compatibility profiles can reload the runtime package while an
        # already-imported adapter still holds this function. Resolve the
        # current module once so a fresh pool is not mistaken for an absent one.
        current_module = sys.modules.get(__name__)
        current_factory = getattr(current_module, "Checkpointer", None)
        if current_factory is not None and current_factory is not Checkpointer:
            return current_factory()
        raise RuntimeError(
            "Checkpointer not initialized; call start_pool()/setup_checkpointer() first "
            "(DATABASE_URI required)"
        )
    return _CHECKPOINTER


def get_checkpointer() -> AsyncPostgresSaver:
    """Return the initialized saver for the production executor."""
    return Checkpointer()


async def get_checkpoint(config: RunnableConfig) -> CheckpointTuple | None:
    """Read the latest durable checkpoint for a graph config."""
    return await get_checkpointer().aget_tuple(config)


async def list_checkpoints(
    config: RunnableConfig | None = None,
    *,
    before: RunnableConfig | None = None,
    limit: int | None = None,
) -> AsyncIterator[CheckpointTuple]:
    """Stream durable checkpoint history without exposing the backing pool."""
    async for item in get_checkpointer().alist(config, before=before, limit=limit):
        yield item


async def put_checkpoint(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: dict[str, Any],
) -> RunnableConfig:
    """Persist a checkpoint using the public LangGraph saver contract."""
    return await get_checkpointer().aput(config, checkpoint, metadata, new_versions)


async def put_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    *,
    task_path: str = "",
) -> None:
    await get_checkpointer().aput_writes(config, writes, task_id, task_path)


async def delete_thread_checkpoints(thread_id: str) -> None:
    """Delete all checkpoints only when deleting the entire thread."""
    await get_checkpointer().adelete_thread(thread_id)


async def copy_thread_checkpoints(source_thread_id: str, target_thread_id: str) -> None:
    """Copy a thread through the public saver contract.

    The pinned postgres saver exposes ``acopy_thread`` but currently raises
    ``NotImplementedError``. Replaying checkpoints oldest-first preserves the
    parent chain while keeping the implementation independent of private SQL.
    """
    saver = get_checkpointer()
    source_config = cast(RunnableConfig, {"configurable": {"thread_id": source_thread_id}})
    items = [item async for item in saver.alist(source_config)]
    items.sort(key=lambda item: str(item.checkpoint.get("id", "")))
    for item in items:
        source_configurable = item.config["configurable"]
        target_configurable: dict[str, Any] = {
            "thread_id": target_thread_id,
            "checkpoint_ns": source_configurable.get("checkpoint_ns", ""),
        }
        parent = item.parent_config
        if parent is not None:
            parent_id = parent.get("configurable", {}).get("checkpoint_id")
            if parent_id:
                target_configurable["checkpoint_id"] = parent_id
        target_config = cast(RunnableConfig, {"configurable": target_configurable})
        checkpoint = item.checkpoint
        next_config = await saver.aput(
            target_config,
            checkpoint,
            item.metadata,
            dict(checkpoint.get("channel_versions", {})),
        )
        for task_id, channel, value in item.pending_writes or ():
            await saver.aput_writes(next_config, [(channel, value)], task_id)


__all__ = [
    "Checkpointer",
    "copy_thread_checkpoints",
    "delete_thread_checkpoints",
    "get_checkpoint",
    "get_checkpointer",
    "list_checkpoints",
    "put_checkpoint",
    "put_writes",
    "reconnect_checkpointer",
    "setup_checkpointer",
    "teardown_checkpointer",
]
