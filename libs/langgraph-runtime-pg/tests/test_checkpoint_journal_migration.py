from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def seed_legacy(uri, schema):
    """Three old-format boundaries include overwrites and a deleted namespace."""
    from langgraph_runtime_pg.migrate import alembic_config
    from test_persistence_contract import _schema_uri

    scoped = _schema_uri(uri, schema)
    cfg = alembic_config(scoped, version_table_schema=schema)
    command.upgrade(cfg, "014_thread_retention")
    tid = uuid4()
    runs = [uuid4() for _ in range(3)]
    snapshots = []
    values = []
    with psycopg.connect(scoped, autocommit=True, row_factory=dict_row) as conn:
        PostgresSaver(conn).setup()
        conn.execute(
            "INSERT INTO threads(thread_id,\"values\",status) VALUES(%s,%s,'idle')",
            (tid, Jsonb({"items": ["seed"]})),
        )
        conn.execute(
            """INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint)
            VALUES(%s,'old-child','old','{"v":4,"channel_versions":{}}')""",
            (str(tid),),
        )
        for i, rid in enumerate(runs):
            before = {
                table: [
                    r["value"]
                    for r in conn.execute(
                        sql.SQL(
                            "SELECT to_jsonb(t) AS value FROM {} t WHERE thread_id=%s ORDER BY to_jsonb(t)::text"
                        ).format(sql.Identifier(table)),
                        (str(tid),),
                    )
                ]
                for table in ("checkpoints", "checkpoint_writes")
            }
            prior = {"items": ["seed", *range(i)]}
            snapshots.append(before)
            values.append(prior)
            conn.execute(
                """INSERT INTO runs(run_id,thread_id,assistant_id,status,retry_count)
                VALUES(%s,%s,%s,'success',1)""",
                (rid, tid, uuid4()),
            )
            conn.execute(
                """INSERT INTO run_checkpoint_baselines(run_id,thread_id,checkpoints,writes,projection)
                VALUES(%s,%s,%s,%s,%s)""",
                (
                    rid,
                    tid,
                    Jsonb(before["checkpoints"]),
                    Jsonb(before["checkpoint_writes"]),
                    Jsonb(
                        {
                            "values_": prior,
                            "status": "idle",
                            "interrupts": {},
                            "error": None,
                            "graph_id": None,
                            "state_updated_at": None,
                            "_captured_at": (datetime.now(UTC) + timedelta(seconds=i)).isoformat(),
                        }
                    ),
                ),
            )
            conn.execute(
                """INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint,metadata)
                VALUES(%s,'','shared',%s,%s) ON CONFLICT(thread_id,checkpoint_ns,checkpoint_id)
                DO UPDATE SET checkpoint=EXCLUDED.checkpoint,metadata=EXCLUDED.metadata""",
                (
                    str(tid),
                    Jsonb({"v": 4, "channel_versions": {}, "channel_values": {"value": i}}),
                    Jsonb({"run_id": str(rid)}),
                ),
            )
            conn.execute(
                """INSERT INTO checkpoint_writes(thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel,type,blob)
                VALUES(%s,'','shared','task',-1,'__error__','bytes',%s)
                ON CONFLICT(thread_id,checkpoint_ns,checkpoint_id,task_id,idx)
                DO UPDATE SET blob=EXCLUDED.blob""",
                (str(tid), bytes([i])),
            )
            if i == 1:
                conn.execute(
                    "DELETE FROM checkpoints WHERE thread_id=%s AND checkpoint_ns='old-child'",
                    (str(tid),),
                )
        conn.execute(
            'UPDATE threads SET "values"=%s WHERE thread_id=%s',
            (Jsonb({"items": ["seed", 0, 1, 2]}), tid),
        )
    return scoped, cfg, tid, runs, snapshots, values


@pytest.mark.parametrize("inject_failure", [False, True])
async def test_old_boundaries_convert_and_reverse_exactly(pg_runtime, inject_failure, tmp_path):
    from langgraph_runtime_pg.checkpoint_mutations import rollback_run
    from langgraph_runtime_pg.database import to_async_sqlalchemy_uri, to_psycopg_uri
    from langgraph_runtime_pg.migrate import head_revision
    from langgraph_runtime_pg.models import RunRow, ThreadRow

    uri = to_psycopg_uri()
    schema = "journal_migration_" + uuid4().hex[:12]
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    engine = None
    try:
        scoped, cfg, tid, runs, snapshots, values = seed_legacy(uri, schema)
        backup = tmp_path / "before-conversion.dump"
        await asyncio.to_thread(
            subprocess.run,
            [
                "pg_dump",
                "--dbname",
                uri,
                "--schema",
                schema,
                "--format=custom",
                "--file",
                str(backup),
            ],
            check=True,
            capture_output=True,
        )
        if inject_failure:
            with psycopg.connect(scoped) as conn:
                conn.execute("UPDATE checkpoints SET metadata='{}' WHERE checkpoint_ns='' ")
            with pytest.raises(RuntimeError, match="cannot prove checkpoint ownership"):
                command.upgrade(cfg, "head")
            with psycopg.connect(scoped) as conn:
                assert (
                    conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                    == "014_thread_retention"
                )
                assert (
                    conn.execute(
                        "SELECT count(*) FROM run_checkpoint_baselines WHERE writes IS NOT NULL"
                    ).fetchone()[0]
                    == 3
                )
                conn.execute(
                    "UPDATE checkpoints SET metadata=%s WHERE checkpoint_ns=''",
                    (Jsonb({"run_id": str(runs[-1])}),),
                )
        command.upgrade(cfg, "head")
        with psycopg.connect(scoped) as conn:
            assert (
                conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                == head_revision()
            )
            assert conn.execute("SELECT count(*) FROM run_checkpoint_undo").fetchone()[0] == 7
            assert (
                conn.execute(
                    "SELECT max(octet_length(projection::text)) FROM run_checkpoint_baselines"
                ).fetchone()[0]
                < 1024
            )
        engine = create_async_engine(
            to_async_sqlalchemy_uri(uri), connect_args={"server_settings": {"search_path": schema}}
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        for index in reversed(range(3)):
            async with sessions.begin() as session:
                run = await session.scalar(
                    select(RunRow).where(RunRow.run_id == runs[index]).with_for_update()
                )
                await rollback_run(session, run)
            with psycopg.connect(scoped, row_factory=dict_row) as conn:
                for table, expected in snapshots[index].items():
                    actual = [
                        r["value"]
                        for r in conn.execute(
                            sql.SQL(
                                "SELECT to_jsonb(t) AS value FROM {} t WHERE thread_id=%s ORDER BY to_jsonb(t)::text"
                            ).format(sql.Identifier(table)),
                            (str(tid),),
                        )
                    ]
                    assert actual == expected
            async with sessions() as session:
                assert (await session.get(ThreadRow, tid)).values_ == values[index]
        await engine.dispose()
        engine = None
        with psycopg.connect(uri, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        await asyncio.to_thread(
            subprocess.run,
            ["pg_restore", "--dbname", uri, "--no-owner", "--no-privileges", str(backup)],
            check=True,
            capture_output=True,
        )
        with psycopg.connect(scoped) as conn:
            assert (
                conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                == "014_thread_retention"
            )
            assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 3
        command.upgrade(cfg, "head")
        engine = create_async_engine(
            to_async_sqlalchemy_uri(uri), connect_args={"server_settings": {"search_path": schema}}
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            run = await session.scalar(
                select(RunRow).where(RunRow.run_id == runs[-1]).with_for_update()
            )
            await rollback_run(session, run)
        async with sessions() as session:
            assert (await session.get(ThreadRow, tid)).values_ == values[-1]
    finally:
        if engine:
            await engine.dispose()
        with psycopg.connect(uri, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
