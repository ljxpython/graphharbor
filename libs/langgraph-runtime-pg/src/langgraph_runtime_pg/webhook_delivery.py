"""Leased, at-least-once delivery. HTTP waits never hold database transactions."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import random
import socket
import ssl
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit
from uuid import UUID, uuid4

import anyio
import httpcore
import structlog
from sqlalchemy import and_, delete, func, or_, select, update

from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.metrics import inc, set_gauge
from langgraph_runtime_pg.models import RunCallbackDeliveryRow as Delivery
from langgraph_runtime_pg.webhooks import settings

logger = structlog.stdlib.get_logger(__name__)
HttpResult = tuple[int, bytes] | tuple[int, bytes, float | None]


def retry_after_seconds(value: bytes | None) -> float | None:
    if value is None or len(value) > 128:
        return None
    try:
        text = value.decode("ascii")
        seconds = (
            float(int(text))
            if text.isdecimal()
            else (parsedate_to_datetime(text) - datetime.now(UTC)).total_seconds()
        )
        return min(300, max(0, seconds))
    except (ValueError, TypeError, UnicodeDecodeError, OverflowError):
        return None


def signed_headers(delivery: Delivery, *, timestamp: int | None = None) -> dict[str, str]:
    cfg = settings()
    if not cfg.key_id or not cfg.secret:
        raise ValueError("terminal webhook signing key is unavailable")
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    path = urlsplit(delivery.target).path or "/"
    prefix = f"{cfg.key_id}\n{stamp}\nPOST\n{path}\n".encode()
    signature = hmac.new(
        cfg.secret.encode(), prefix + delivery.body.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "X-Run-Completion-Key-Id": cfg.key_id,
        "X-Run-Completion-Timestamp": stamp,
        "X-Run-Completion-Event-Id": str(delivery.event_id),
        "X-Run-Completion-Signature": "sha256=" + signature,
    }


class ValidatedBackend(httpcore.AsyncNetworkBackend):
    """Resolve and validate the same numeric address used for the TCP connection."""

    def __init__(self, *, managed: bool) -> None:
        self.managed = managed
        self.backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ):
        with anyio.fail_after(timeout):
            addresses = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            for _, _, _, _, address in addresses:
                settings().validate_ip(address[0], managed=self.managed)
            return await self.backend.connect_tcp(
                addresses[0][4][0], port, timeout, local_address, socket_options
            )

    async def sleep(self, seconds: float) -> None:
        await anyio.sleep(seconds)


async def send_http(delivery: Delivery) -> HttpResult:
    cfg = settings()
    managed = delivery.profile == "terminal"
    target = cfg.validate_url(delivery.target, managed=managed)
    headers: Any = {**cfg.headers, "Content-Type": "application/json"}
    if managed:
        if target != cfg.terminal_url:
            raise ValueError(
                "terminal webhook destination changed; replay requires an explicit migration"
            )
        headers.update(signed_headers(delivery))
    if target.startswith("/"):
        base = os.environ.get("GRAPHHARBOR_WEBHOOK_BASE_URL")
        if not base:
            raise ValueError("relative webhooks require GRAPHHARBOR_WEBHOOK_BASE_URL")
        target = urljoin(base, target)
    async with httpcore.AsyncConnectionPool(
        network_backend=ValidatedBackend(managed=managed), ssl_context=ssl.create_default_context()
    ) as pool:
        for redirects in range(6):
            with anyio.fail_after(35):
                async with pool.stream(
                    "POST",
                    target,
                    content=delivery.body.encode(),
                    headers=headers,
                    extensions={
                        "timeout": {"connect": 5.0, "read": 30.0, "write": 5.0, "pool": 5.0}
                    },
                ) as response:
                    body = bytearray()
                    async for chunk in response.aiter_stream():
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise ValueError("webhook response exceeds the limit")
                    if managed or response.status not in {301, 302, 303, 307, 308}:
                        if response.status in {429, 503}:
                            return (
                                response.status,
                                bytes(body),
                                retry_after_seconds(dict(response.headers).get(b"retry-after")),
                            )
                        return response.status, bytes(body)
                    location = dict(response.headers).get(b"location")
                    if not location or redirects == 5:
                        return response.status, bytes(body)
                    next_target = cfg.validate_url(urljoin(target, location.decode()))
                    # Static credentials only stay on the original origin.
                    if urlsplit(next_target).netloc != urlsplit(target).netloc:
                        headers = {"Content-Type": "application/json"}
                    target = next_target
    raise ValueError("webhook redirect limit reached")


async def claim_delivery(owner: str) -> Delivery | None:
    if settings().disabled:
        return None
    async with connect() as conn:
        now = await conn.session.scalar(select(func.clock_timestamp()))
        await conn.session.execute(
            update(Delivery)
            .where(
                Delivery.retry_deadline < now,
                or_(
                    Delivery.state == "pending",
                    and_(Delivery.state == "delivering", Delivery.lease_until <= now),
                ),
            )
            .values(
                state="dead_letter",
                lease_owner=None,
                lease_until=None,
                last_error_code="retry_deadline",
            )
        )
        row = await conn.session.scalar(
            select(Delivery)
            .where(
                Delivery.profile.in_(
                    ["standard", "terminal"] if settings().terminal_url else ["standard"]
                ),
                or_(
                    and_(Delivery.state == "pending", Delivery.next_attempt_at <= now),
                    and_(Delivery.state == "delivering", Delivery.lease_until <= now),
                ),
            )
            .order_by(Delivery.next_attempt_at, Delivery.delivery_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        row.state, row.lease_owner = "delivering", owner
        row.lease_until = now + timedelta(seconds=60)
        row.generation += 1
        row.attempts += 1
        await conn.session.flush()
        conn.session.expunge(row)
        return row


def _outcome(row: Delivery, status: int, body: bytes) -> tuple[bool, str | None]:
    if 200 <= status < 300:
        if row.profile == "standard":
            return True, None
        try:
            ack = json.loads(body)
            if (
                isinstance(ack, dict)
                and ack.get("accepted") is True
                and ack.get("event_id") == str(row.event_id)
            ):
                return True, None
        except (ValueError, UnicodeDecodeError):
            pass
        return False, "invalid_ack"
    return False, "http_retryable" if status in {
        408,
        425,
        429,
    } or status >= 500 else "http_rejected"


async def deliver_once(
    owner: str, sender: Callable[[Delivery], Awaitable[HttpResult]] = send_http
) -> bool:
    row = await claim_delivery(owner)
    if row is None:
        return False
    status = None
    retry_after = None
    try:
        with anyio.fail_after(40):
            response = await sender(row)
            status, body = response[:2]
            retry_after = response[2] if len(response) == 3 else None
        success, error = _outcome(row, status, body)
    except (httpcore.NetworkError, httpcore.TimeoutException, TimeoutError, OSError):
        success, error = False, "transport_error"
    except ValueError:
        success, error = False, "policy_rejected"
    except Exception:
        success, error = False, "transport_error"
        logger.warning("webhook sender failed", delivery_id=str(row.delivery_id))
    now = datetime.now(UTC)
    retry = error in {"transport_error", "http_retryable"} and now < row.retry_deadline
    state = "delivered" if success else "pending" if retry else "dead_letter"
    delay = min(300, 2 ** min(row.attempts, 9) * random.uniform(0.8, 1.2))
    if status in {429, 503} and retry_after is not None:
        delay = max(delay, min(300, max(0, retry_after)))
    async with connect() as conn:
        result = await conn.session.execute(
            update(Delivery)
            .where(
                Delivery.delivery_id == row.delivery_id,
                Delivery.lease_owner == owner,
                Delivery.generation == row.generation,
                Delivery.state == "delivering",
            )
            .values(
                state=state,
                lease_owner=None,
                lease_until=None,
                last_http_status=status,
                last_error_code=error,
                next_attempt_at=now + timedelta(seconds=delay),
                delivered_at=now if success else None,
            )
        )
    inc("graphharbor_webhook_attempts_total", labels={"profile": row.profile, "outcome": state})
    if result.rowcount and state == "dead_letter":
        logger.warning(
            "webhook delivery requires replay", delivery_id=str(row.delivery_id), error_code=error
        )
    return True


async def replay_delivery(delivery_id: UUID) -> bool:
    now = datetime.now(UTC)
    async with connect() as conn:
        result = await conn.session.execute(
            update(Delivery)
            .where(
                Delivery.delivery_id == delivery_id,
                Delivery.state == "dead_letter",
            )
            .values(
                state="pending",
                next_attempt_at=now,
                retry_deadline=now + timedelta(hours=24),
                last_error_code=None,
            )
        )
        return bool(result.rowcount)


async def maintain_deliveries(
    action: str, delivery_id: UUID | None = None
) -> list[dict[str, Any]] | bool | int:
    if action in {"replay", "discard", "confirm-stop"} and delivery_id is None:
        raise ValueError("This webhook action requires an identifier")
    if action == "replay":
        assert delivery_id is not None
        return await replay_delivery(delivery_id)
    if action == "confirm-stop":
        from langgraph_runtime_pg.models import RunRow
        from langgraph_runtime_pg.run_store import RunRepository

        async with connect() as conn:
            run = await conn.session.get(RunRow, delivery_id, with_for_update=True)
            if run is None:
                return False
            result = await RunRepository().confirm_fenced_stop(conn.session, run, run.retry_count)
            if result:
                logger.warning(
                    "operator confirmed fenced execution stopped", run_id=str(delivery_id)
                )
            return result is not None
    async with connect() as conn:
        if action == "list":
            rows = (
                await conn.session.scalars(
                    select(Delivery)
                    .where(
                        Delivery.state.in_(
                            ["pending", "delivering", "dead_letter", "awaiting_stop"]
                        )
                    )
                    .order_by(Delivery.created_at)
                    .limit(100)
                )
            ).all()
            return [
                {
                    "delivery_id": str(row.delivery_id),
                    "event_id": str(row.event_id),
                    "run_id": str(row.run_id),
                    "profile": row.profile,
                    "state": row.state,
                    "attempts": row.attempts,
                    "last_error_code": row.last_error_code,
                }
                for row in rows
            ]
        if action == "discard":
            result = await conn.session.execute(
                update(Delivery)
                .where(
                    Delivery.delivery_id == delivery_id,
                    Delivery.state.in_(["pending", "dead_letter", "awaiting_stop"]),
                )
                .values(
                    state="discarded",
                    delivered_at=datetime.now(UTC),
                    last_error_code="operator_discarded",
                )
            )
            if result.rowcount:
                logger.warning("webhook explicitly discarded", delivery_id=str(delivery_id))
            return bool(result.rowcount)
        if action != "cleanup":
            raise ValueError("Unknown webhook maintenance action")
        result = await conn.session.execute(
            delete(Delivery).where(
                Delivery.state.in_(["delivered", "discarded"]),
                Delivery.delivered_at < datetime.now(UTC) - timedelta(days=30),
            )
        )
        return result.rowcount


async def _delivery_slot() -> None:
    owner = uuid4().hex
    while True:
        try:
            if await deliver_once(owner):
                continue
            async with connect() as conn:
                counts = (
                    await conn.session.execute(
                        select(Delivery.state, func.count()).group_by(Delivery.state)
                    )
                ).all()
                oldest = await conn.session.scalar(
                    select(func.min(Delivery.created_at)).where(
                        Delivery.state.in_(["pending", "delivering", "awaiting_stop"])
                    )
                )
            for state in (
                "pending",
                "delivering",
                "dead_letter",
                "awaiting_stop",
                "delivered",
                "discarded",
            ):
                set_gauge(
                    "graphharbor_webhook_deliveries",
                    dict(counts).get(state, 0),
                    labels={"state": state},
                )
            set_gauge(
                "graphharbor_webhook_oldest_pending_seconds",
                max(0, int((datetime.now(UTC) - oldest).total_seconds())) if oldest else 0,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("webhook dispatcher will retry")
        await asyncio.sleep(1)


async def delivery_loop() -> None:
    async with asyncio.TaskGroup() as group:
        for _ in range(4):
            group.create_task(_delivery_slot())
