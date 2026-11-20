"""A real five-minute receiver outage preserves and drains durable deliveries."""

import asyncio
import hashlib
import hmac
import json
import os
import socket
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import uvicorn
from sqlalchemy import func, select
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.models import RunCallbackDeliveryRow as Delivery, RunRow
from langgraph_runtime_pg.webhook_delivery import delivery_loop
from langgraph_runtime_pg.webhooks import configure_webhooks

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_COMPLETION_BACKLOG_TEST") != "1", reason="five-minute outage opt-in"
)


async def test_two_dispatchers_drain_after_five_minute_outage(pg_runtime, monkeypatch, tmp_path):
    secret = "backlog-fixture-secret-at-least-32-bytes"
    accepted = set()
    recovered = False
    requests = 0

    async def receive(request):
        nonlocal requests
        requests += 1
        raw = await request.body()
        canonical = (
            f"key\n{request.headers['x-run-completion-timestamp']}\nPOST\n/hook\n".encode() + raw
        )
        assert hmac.compare_digest(
            request.headers["x-run-completion-signature"],
            "sha256=" + hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest(),
        )
        if not recovered:
            return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
        event_id = json.loads(raw)["event_id"]
        accepted.add(event_id)
        return JSONResponse({"accepted": True, "event_id": event_id})

    monkeypatch.setenv("GRAPHHARBOR_TERMINAL_WEBHOOK_KEY_ID", "key")
    monkeypatch.setenv("GRAPHHARBOR_TERMINAL_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("GRAPHHARBOR_WEBHOOK_ALLOW_HTTP", "1")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        target = f"http://127.0.0.1:{listener.getsockname()[1]}/hook"
        configure_webhooks({"webhooks": {"terminal": {"enabled": True, "url": target}}})
        now = datetime.now(UTC)
        async with connect() as conn:
            for _ in range(20):
                event_id = uuid4()
                conn.session.add(
                    Delivery(
                        event_id=event_id,
                        run_id=uuid4(),
                        profile="terminal",
                        target=target,
                        body=json.dumps({"event_id": str(event_id)}),
                        state="pending",
                        created_at=now,
                        next_attempt_at=now,
                        retry_deadline=now + timedelta(hours=24),
                    )
                )
        server = uvicorn.Server(
            uvicorn.Config(
                Starlette(routes=[Route("/hook", receive, methods=["POST"])]),
                lifespan="off",
                log_level="critical",
            )
        )
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        dispatchers = [asyncio.create_task(delivery_loop()) for _ in range(2)]
        started = time.monotonic()
        try:
            await asyncio.sleep(300)
            async with connect() as conn:
                assert (
                    await conn.session.scalar(
                        select(func.count())
                        .select_from(Delivery)
                        .where(Delivery.state == "pending")
                    )
                    == 20
                )
            assert not accepted and requests > 20
            recovered = True
            recovery_at = time.monotonic()
            async with asyncio.timeout(360):
                while True:
                    async with connect() as conn:
                        delivered = await conn.session.scalar(
                            select(func.count())
                            .select_from(Delivery)
                            .where(Delivery.state == "delivered")
                        )
                    if delivered == 20:
                        break
                    await asyncio.sleep(0.1)
            assert len(accepted) == 20
            async with connect() as conn:
                assert await conn.session.scalar(select(func.count()).select_from(RunRow)) == 0
            evidence = {
                "receiver_outage_seconds": round(recovery_at - started, 3),
                "recovery_seconds": round(time.monotonic() - recovery_at, 3),
                "snapshots": 20,
                "unique_acks": len(accepted),
                "dispatchers": 2,
                "http_attempts": requests,
                "agent_runs_created": 0,
            }
            (tmp_path / "backlog-evidence.json").write_text(json.dumps(evidence, indent=2))
            print(json.dumps(evidence))
        finally:
            for task in dispatchers:
                task.cancel()
            await asyncio.gather(*dispatchers, return_exceptions=True)
            server.should_exit = True
            await server_task
            configure_webhooks({})
