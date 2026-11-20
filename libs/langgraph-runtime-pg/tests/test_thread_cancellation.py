"""Real PostgreSQL boundaries: fixed targets, retry, claim and later submissions."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update
from starlette.applications import Starlette
from starlette.routing import Route

from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.models import RunRow, ThreadRow
from langgraph_runtime_pg.run_store import RunRepository
from langhost.cancellation import cancel_active, get_cancellation

pytestmark = pytest.mark.asyncio


def app():
    return Starlette(
        routes=[
            Route("/threads/{thread_id}/runs/cancel-active", cancel_active, methods=["POST"]),
            Route(
                "/threads/{thread_id}/runs/cancellations/{cancellation_id}",
                get_cancellation,
                methods=["GET"],
            ),
        ]
    )


async def seed(count):
    thread, assistant = uuid4(), uuid4()
    async with connect() as conn:
        conn.session.add(ThreadRow(thread_id=thread))
        await conn.session.flush()
        for _ in range(count):
            await RunRepository().create(
                conn.session,
                assistant_id=assistant,
                thread_id=thread,
                kwargs={},
                metadata={},
                multitask_strategy="enqueue",
            )
    return thread, assistant


async def test_fixed_snapshot_pagination_retries_and_new_run(pg_runtime):
    thread, assistant = await seed(151)
    stop = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app()), base_url="http://test"
    ) as client:
        path = f"/threads/{thread}/runs/cancel-active"
        kwargs = {
            "json": {"cancellation_id": str(stop), "action": "interrupt"},
            "headers": {"Idempotency-Key": "stop"},
        }
        response = await client.post(path, **kwargs)
        assert response.status_code == 202, response.text
        receipt = response.json()
        assert receipt["target_count"] == 151 and receipt["execution_stopped"] is True
        assert len(receipt["targets"]) == 100 and receipt["next_offset"] == 100
        async with connect() as conn:
            new = await RunRepository().create(
                conn.session,
                assistant_id=assistant,
                thread_id=thread,
                kwargs={},
                metadata={},
                multitask_strategy="enqueue",
            )
        again = await client.post(path, **kwargs)
        assert again.json() == receipt
        tail = await client.get(f"/threads/{thread}/runs/cancellations/{stop}?offset=100")
        assert len(tail.json()["targets"]) == 51
        bad = await client.post(
            path,
            json={"cancellation_id": str(uuid4()), "action": "interrupt"},
            headers=kwargs["headers"],
        )
        assert bad.status_code == 409
        assert (
            await client.post(
                path,
                json={"action": "rollback", "cancellation_id": str(stop)},
                headers=kwargs["headers"],
            )
        ).status_code == 422
    async with connect() as conn:
        assert (await conn.session.get(RunRow, new.run_id)).status == "pending"


@pytest.mark.parametrize("attempt", range(20))
async def test_claim_cancel_and_submit_compete_without_partial_snapshot(pg_runtime, attempt):
    thread, assistant = await seed(8)

    async def claim():
        async with connect() as conn:
            return await RunRepository().claim_next(conn.session, owner="worker")

    async def submit():
        async with connect() as conn:
            return await RunRepository().create(
                conn.session,
                assistant_id=assistant,
                thread_id=thread,
                kwargs={},
                metadata={},
                multitask_strategy="enqueue",
            )

    stop = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app()), base_url="http://test"
    ) as client:

        async def cancel():
            return await client.post(
                f"/threads/{thread}/runs/cancel-active",
                json={"cancellation_id": str(stop), "action": "interrupt"},
                headers={"Idempotency-Key": "race"},
            )

        response, claimed, submitted = await asyncio.gather(cancel(), claim(), submit())
        if response.status_code == 409:
            response = await cancel()
        assert response.status_code == 202, response.text
        targets = {t["run_id"] for t in response.json()["targets"]}
        assert len(targets) in {8, 9}
        async with connect() as conn:
            rows = list(
                await conn.session.scalars(select(RunRow).where(RunRow.thread_id == thread))
            )
            assert all(r.status == "interrupted" for r in rows if str(r.run_id) in targets)
            assert all(
                r.run_id == submitted.run_id and r.status == "pending"
                for r in rows
                if str(r.run_id) not in targets
            )
        if claimed is not None and str(claimed.run_id) in targets:
            assert response.json()["execution_stopped"] is False


async def test_lease_fence_is_unconfirmed_until_execution_receipt(pg_runtime):
    thread, _ = await seed(1)
    async with connect() as conn:
        run = await RunRepository().claim_next(conn.session, owner="worker")
    stop = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app()), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/threads/{thread}/runs/cancel-active",
            json={"cancellation_id": str(stop), "action": "interrupt"},
            headers={"Idempotency-Key": "fence"},
        )
        assert response.status_code == 202
        assert response.json()["execution_stopped"] is False
        assert response.json()["confirmation_unavailable"] is False
        async with connect() as conn:
            await conn.session.execute(
                update(RunRow)
                .where(RunRow.run_id == run.run_id)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await RunRepository().requeue_expired(conn.session)
        receipt = (await client.get(f"/threads/{thread}/runs/cancellations/{stop}")).json()
        assert receipt["execution_stopped"] is False
        assert receipt["confirmation_unavailable"] is True


@pytest.mark.parametrize("count", [1, 10, 100, 500])
async def test_capacity_baseline_fixed_receipt(pg_runtime, count, record_property):
    from sqlalchemy import event

    from langgraph_runtime_pg import database

    thread, _ = await seed(count)
    stop = uuid4()
    queries = []

    def query(*args):
        queries.append(1)

    engine = database._ENGINE.sync_engine
    event.listen(engine, "before_cursor_execute", query)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app()), base_url="http://test"
        ) as client:
            started = time.monotonic()
            response = await client.post(
                f"/threads/{thread}/runs/cancel-active",
                json={"cancellation_id": str(stop), "action": "interrupt"},
                headers={"Idempotency-Key": "capacity"},
            )
            duration = time.monotonic() - started
            assert response.status_code == 202 and response.json()["target_count"] == count
            assert response.json()["execution_stopped"] is True
            ids = {target["run_id"] for target in response.json()["targets"]}
            offset = response.json()["next_offset"]
            while offset is not None:
                page = (
                    await client.get(
                        f"/threads/{thread}/runs/cancellations/{stop}", params={"offset": offset}
                    )
                ).json()
                ids.update(target["run_id"] for target in page["targets"])
                offset = page["next_offset"]
            assert len(ids) == count
        metrics = {
            "targets": count,
            "submit_ms": round(duration * 1000, 2),
            "sql_statements_including_pages": len(queries),
        }
        record_property("cancellation_capacity", json.dumps(metrics))
        print(json.dumps(metrics))
    finally:
        event.remove(engine, "before_cursor_execute", query)
