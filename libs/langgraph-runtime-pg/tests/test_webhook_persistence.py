"""Durable callbacks use disposable PostgreSQL, including cancellation fencing."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from langgraph_runtime_pg.auth import sign_runtime_context
from langgraph_runtime_pg.database import connect, start_pool, stop_pool
from langgraph_runtime_pg.models import (
    AssistantRow,
    RunCallbackDeliveryRow as Delivery,
    RunRow,
    ThreadRow,
)
from langgraph_runtime_pg.run_store import RunRepository
from langgraph_runtime_pg.webhook_delivery import (
    claim_delivery,
    deliver_once,
    maintain_deliveries,
    replay_delivery,
)
from langgraph_runtime_pg.webhooks import configure_webhooks


@pytest.fixture(autouse=True)
def policy(monkeypatch):
    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_SECRET", "r" * 32)
    monkeypatch.setenv("GRAPHHARBOR_TERMINAL_WEBHOOK_KEY_ID", "runtime-v1")
    monkeypatch.setenv("GRAPHHARBOR_TERMINAL_WEBHOOK_SECRET", "s" * 32)
    configure_webhooks(
        {
            "webhooks": {
                "terminal": {
                    "enabled": True,
                    "url": "https://api.test/api/runtime/internal/run-completion",
                }
            }
        }
    )
    yield
    configure_webhooks({})


async def seed(*, webhook=None):
    run_id, thread_id, assistant_id, origin_ref = (uuid4() for _ in range(4))
    token = sign_runtime_context(
        {
            "auth_user": {
                "identity": "test-user",
                "callback_context": {"origin_ref": str(origin_ref)},
            },
            "permissions": [],
        },
        run_id=str(run_id),
        thread_id=str(thread_id),
    )
    repository = RunRepository()
    async with connect() as conn:
        conn.session.add(AssistantRow(assistant_id=assistant_id, graph_id="probe", name="probe"))
        conn.session.add(ThreadRow(thread_id=thread_id))
        await conn.session.flush()
        run = await repository.create(
            conn.session,
            thread_id=thread_id,
            assistant_id=assistant_id,
            kwargs={"runtime_context_token": token, **({"webhook": webhook} if webhook else {})},
            metadata={},
        )
        run_id = run.run_id
        run.kwargs = {
            **run.kwargs,
            "runtime_context_token": sign_runtime_context(
                {
                    "auth_user": {
                        "identity": "test-user",
                        "callback_context": {"origin_ref": str(origin_ref)},
                    },
                    "permissions": [],
                },
                run_id=str(run_id),
                thread_id=str(thread_id),
            ),
        }
    async with connect() as conn:
        run = await repository.claim_next(conn.session, "worker")
        assert run.run_id == run_id
        generation = run.retry_count
    return repository, run_id, thread_id, generation


@pytest.mark.parametrize(
    "status,reason",
    [
        ("success", "completed"),
        ("error", "business_error"),
        ("timeout", "timeout"),
        ("interrupted", "hitl_interrupt"),
    ],
)
async def test_atomic_terminal_snapshot_survives_run_delete_and_restart(pg_runtime, status, reason):
    repository, run_id, _, generation = await seed()
    async with connect() as conn:
        await repository.finish(
            conn.session, run_id, "worker", status, reason=reason, generation=generation
        )
        event_id = repository.last_transition_events[0].event_id
    async with connect() as conn:
        assert (
            await repository.finish(conn.session, run_id, "worker", status, reason=reason) is None
        )
        row = await conn.session.scalar(select(Delivery))
        body = json.loads(row.body)
        assert body["event_id"] == str(event_id) and body["graph_id"] == "probe"
        assert body["execution_stopped"] is True and "runtime_context_token" not in row.body
        await conn.session.delete(await conn.session.get(RunRow, run_id))
    await stop_pool()
    await start_pool()
    async with connect() as conn:
        assert await conn.session.scalar(select(func.count()).select_from(Delivery)) == 1
        assert await conn.session.get(RunRow, run_id) is None


async def test_snapshot_rolls_back_with_terminal_transaction(pg_runtime):
    repository, run_id, _, generation = await seed()
    with pytest.raises(RuntimeError, match="crash"):
        async with connect() as conn:
            await repository.finish(
                conn.session,
                run_id,
                "worker",
                "error",
                reason="business_error",
                generation=generation,
            )
            raise RuntimeError("crash before commit")
    async with connect() as conn:
        assert (await conn.session.get(RunRow, run_id)).status == "running"
        assert await conn.session.scalar(select(func.count()).select_from(Delivery)) == 0


async def test_fenced_cancel_waits_for_same_generation_and_reuses_event(pg_runtime):
    repository, run_id, _, generation = await seed()
    async with connect() as conn:
        run = await conn.session.get(RunRow, run_id)
        run.reason = "cancel_requested"
        run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await conn.session.flush()
        await repository.requeue_expired(conn.session)
        event_id = repository.last_requeued_events[0].event_id
        delivery = await conn.session.scalar(select(Delivery))
        assert delivery.state == "awaiting_stop" and delivery.event_id == event_id
    async with connect() as conn:
        run = await conn.session.get(RunRow, run_id, with_for_update=True)
        assert await repository.confirm_fenced_stop(conn.session, run, generation - 1) is None
        configure_webhooks({"http": {"disable_webhooks": True}})
        event = await repository.confirm_fenced_stop(conn.session, run, generation)
        assert event.event_id == event_id and event.payload["execution_stopped"] is True
    async with connect() as conn:
        assert await conn.session.scalar(select(func.count()).select_from(Delivery)) == 1
        row = await conn.session.scalar(select(Delivery))
        assert row.state == "pending" and json.loads(row.body)["execution_stopped"] is True


async def test_retry_after_delays_only_snapshot_without_rerunning_agent(pg_runtime):
    repository, run_id, _, generation = await seed()
    async with connect() as conn:
        await repository.finish(
            conn.session, run_id, "worker", "error", reason="business_error", generation=generation
        )

    async def throttled(row):
        return 429, b"", 9999.0

    assert await deliver_once("rate-limited", throttled)
    async with connect() as conn:
        row = await conn.session.scalar(select(Delivery))
        assert row.state == "pending" and row.attempts == 1
        assert 295 <= (row.next_attempt_at - datetime.now(UTC)).total_seconds() <= 300
        assert (await conn.session.get(RunRow, run_id)).status == "error"


async def test_rollback_keeps_canonical_id_and_waits_for_worker(pg_runtime):
    from langgraph_runtime_pg.checkpoint_mutations import complete_rollbacks, rollback_run

    _, run_id, _, generation = await seed()
    async with connect() as conn:
        run = await conn.session.get(RunRow, run_id, with_for_update=True)
        await rollback_run(conn.session, run, execution_stopped=False)
        delivery = await conn.session.scalar(select(Delivery))
        assert delivery.state == "awaiting_stop"
        event_id = delivery.event_id
    assert await claim_delivery("dispatcher") is None
    await complete_rollbacks(owner="other", run_id=run_id, generation=generation)
    assert await claim_delivery("dispatcher") is None
    await complete_rollbacks(owner="worker", run_id=run_id, generation=generation)
    delivery = await claim_delivery("dispatcher")
    assert delivery.event_id == event_id and json.loads(delivery.body)["execution_stopped"] is True


async def test_two_dispatchers_retry_ack_loss_lease_takeover_and_deadletter(pg_runtime):
    repository, run_id, _, generation = await seed()
    async with connect() as conn:
        await repository.finish(
            conn.session, run_id, "worker", "error", reason="business_error", generation=generation
        )
    rows = await asyncio.gather(claim_delivery("one"), claim_delivery("two"))
    assert sum(row is not None for row in rows) == 1
    async with connect() as conn:
        await conn.session.execute(
            update(Delivery).values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    takeover = await claim_delivery("three")
    assert takeover.generation == 2 and takeover.attempts == 2
    async with connect() as conn:
        await conn.session.execute(
            update(Delivery).values(state="pending", next_attempt_at=datetime.now(UTC))
        )

    async def fail(row):
        return 503, b"private-canary"

    assert await deliver_once("four", fail)
    async with connect() as conn:
        row = await conn.session.scalar(select(Delivery))
        assert row.state == "pending" and row.last_error_code == "http_retryable"
        row.next_attempt_at = datetime.now(UTC)

    async def bad_ack(row):
        return 200, b'{"accepted":true,"event_id":"wrong"}'

    assert await deliver_once("five", bad_ack)
    assert (await maintain_deliveries("list"))[0]["state"] == "dead_letter"
    assert await replay_delivery(takeover.delivery_id)

    async def ack(row):
        return 200, json.dumps({"accepted": True, "event_id": str(row.event_id)}).encode()

    assert await deliver_once("six", ack)
    async with connect() as conn:
        assert (await conn.session.get(Delivery, takeover.delivery_id)).state == "delivered"


async def test_disable_pause_and_retention_do_not_discard_pending(pg_runtime):
    repository, run_id, _, generation = await seed()
    async with connect() as conn:
        await repository.finish(
            conn.session, run_id, "worker", "error", reason="business_error", generation=generation
        )
        delivery = await conn.session.scalar(select(Delivery))
        delivery.created_at = datetime.now(UTC) - timedelta(days=40)
        delivery_id = delivery.delivery_id
    configure_webhooks({"http": {"disable_webhooks": True}})
    assert await claim_delivery("disabled") is None
    assert await maintain_deliveries("cleanup") == 0
    assert await maintain_deliveries("discard", delivery_id)


async def test_empty_allowed_fields_keeps_standard_and_managed_profiles_separate(pg_runtime):
    configure_webhooks(
        {
            "webhooks": {
                "allowed_fields": [],
                "terminal": {
                    "enabled": True,
                    "url": "https://api.test/api/runtime/internal/run-completion",
                },
            }
        }
    )
    repository, run_id, _, generation = await seed(webhook="https://example.com/hook")
    async with connect() as conn:
        await repository.finish(
            conn.session, run_id, "worker", "success", reason="completed", generation=generation
        )
        rows = (await conn.session.scalars(select(Delivery))).all()
        assert len(rows) == 2
        assert json.loads(next(row.body for row in rows if row.profile == "standard")) == {}
        assert json.loads(next(row.body for row in rows if row.profile == "terminal"))[
            "run_id"
        ] == str(run_id)


async def test_ack_then_sender_crash_replays_same_event_with_rotated_key(pg_runtime, monkeypatch):
    from langgraph_runtime_pg.webhook_delivery import signed_headers

    repository, run_id, _, generation = await seed()
    async with connect() as conn:
        await repository.finish(
            conn.session, run_id, "worker", "error", reason="business_error", generation=generation
        )
    accepted = []

    async def ack_then_crash(row):
        accepted.append(row.event_id)
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await deliver_once("crashed", ack_then_crash)
    async with connect() as conn:
        row = await conn.session.scalar(select(Delivery))
        assert row.state == "delivering"
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    monkeypatch.setenv("GRAPHHARBOR_TERMINAL_WEBHOOK_KEY_ID", "rotated")
    monkeypatch.setenv("GRAPHHARBOR_TERMINAL_WEBHOOK_SECRET", "n" * 32)
    configure_webhooks(
        {
            "webhooks": {
                "terminal": {
                    "enabled": True,
                    "url": "https://api.test/api/runtime/internal/run-completion",
                }
            }
        }
    )

    async def duplicate_ack(row):
        assert signed_headers(row)["X-Run-Completion-Key-Id"] == "rotated"
        accepted.append(row.event_id)
        return 200, json.dumps({"accepted": True, "event_id": str(row.event_id)}).encode()

    await deliver_once("recovered", duplicate_ack)
    assert len(accepted) == 2 and len(set(accepted)) == 1
    async with connect() as conn:
        assert (await conn.session.scalar(select(Delivery))).state == "delivered"


async def test_final_reaper_failure_requires_worker_exit_confirmation(pg_runtime):
    from unittest.mock import AsyncMock

    from langgraph_runtime_pg.production_worker import ProductionWorker

    repository, run_id, _, generation = await seed()
    repository.max_attempts = 1
    async with connect() as conn:
        run = await conn.session.get(RunRow, run_id)
        run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    async with connect() as conn:
        await repository.requeue_expired(conn.session)
        assert (await conn.session.scalar(select(Delivery))).state == "awaiting_stop"
    worker = ProductionWorker(None, owner="worker")
    worker._fanout_durable_event = AsyncMock()
    await worker._confirm_shutdown(run_id, generation, asyncio.Event(), {})
    await worker._confirm_shutdown(run_id, generation, asyncio.Event(), {})
    async with connect() as conn:
        rows = (await conn.session.scalars(select(Delivery))).all()
        assert len(rows) == 1 and json.loads(rows[0].body)["execution_stopped"] is True


async def test_retry_deadline_becomes_replayable_dead_letter(pg_runtime):
    repository, run_id, _, generation = await seed()
    async with connect() as conn:
        await repository.finish(
            conn.session, run_id, "worker", "error", reason="business_error", generation=generation
        )
        row = await conn.session.scalar(select(Delivery))
        row.retry_deadline = datetime.now(UTC) - timedelta(seconds=1)
    assert await claim_delivery("late") is None
    pending = await maintain_deliveries("list")
    assert (
        pending[0]["state"] == "dead_letter" and pending[0]["last_error_code"] == "retry_deadline"
    )
