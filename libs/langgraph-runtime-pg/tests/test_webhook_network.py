import json
import socket
import ssl
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from langgraph_runtime_pg import webhook_delivery as delivery
from langgraph_runtime_pg.webhooks import configure_webhooks


@pytest.mark.parametrize(
    "raw,expected", [(b"60", 60), (b"9999", 300), (b"bad", None), (None, None)]
)
def test_retry_after_is_bounded(raw, expected):
    assert delivery.retry_after_seconds(raw) == expected


def test_retry_after_http_date():
    future = datetime.now(UTC) + timedelta(seconds=60)
    assert 58 <= delivery.retry_after_seconds(format_datetime(future, usegmt=True).encode()) <= 60


@pytest.fixture(autouse=True)
def reset():
    configure_webhooks({})
    yield
    configure_webhooks({})


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "10.0.0.2", "169.254.169.254", "::1", "::ffff:127.0.0.1"]
)
async def test_dns_resolution_blocks_private_addresses_before_connect(monkeypatch, host):
    backend = delivery.ValidatedBackend(managed=False)
    connect = AsyncMock()
    backend.backend = SimpleNamespace(connect_tcp=connect)
    monkeypatch.setattr(
        delivery.anyio,
        "getaddrinfo",
        AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 443)),
            ]
        ),
    )
    with pytest.raises(ValueError):
        await backend.connect_tcp("rebind.test", 443, 1)
    connect.assert_not_awaited()


async def test_tcp_connects_to_the_exact_validated_address(monkeypatch):
    backend = delivery.ValidatedBackend(managed=False)
    connect = AsyncMock()
    backend.backend = SimpleNamespace(connect_tcp=connect)
    monkeypatch.setattr(
        delivery.anyio,
        "getaddrinfo",
        AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            ]
        ),
    )
    await backend.connect_tcp("public.test", 443, 1)
    assert connect.call_args.args[0] == "8.8.8.8"


@pytest.mark.parametrize("managed", [True, False])
async def test_redirect_policy_and_verified_tls(monkeypatch, managed):
    monkeypatch.setenv("GRAPHHARBOR_TERMINAL_WEBHOOK_KEY_ID", "key")
    monkeypatch.setenv("GRAPHHARBOR_TERMINAL_WEBHOOK_SECRET", "s" * 32)
    target = "https://first.test/hook"
    configure_webhooks(
        {
            "webhooks": {
                "headers": {"Authorization": "secret"},
                "terminal": {"enabled": managed, "url": target},
            }
        }
    )
    calls = []

    class Response:
        def __init__(self, status):
            self.status = status
            self.headers = [(b"location", b"https://second.test/hook")]

        async def aiter_stream(self):
            yield b"{}"

    class Pool:
        def __init__(self, **kwargs):
            context = kwargs["ssl_context"]
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        @asynccontextmanager
        async def stream(self, method, url, **kwargs):
            calls.append((url, dict(kwargs["headers"])))
            yield Response(307 if len(calls) == 1 else 200)

    monkeypatch.setattr(delivery.httpcore, "AsyncConnectionPool", Pool)
    row = SimpleNamespace(
        profile="terminal" if managed else "standard",
        target=target,
        body=json.dumps({"event_id": str(uuid4())}),
        event_id=uuid4(),
    )
    status, _ = await delivery.send_http(row)
    assert len(calls) == (1 if managed else 2)
    assert status == (307 if managed else 200)
    if not managed:
        assert calls[1][1] == {"Content-Type": "application/json"}


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/x",
        "https://example.test/x?secret=y",
        "https://user:pass@example.test/x",
        "https://example.test/x\n",
        "https://example.test/x#fragment",
    ],
)
def test_managed_destination_fails_closed(url):
    from langgraph_runtime_pg.webhooks import WebhookSettings

    with pytest.raises(ValueError):
        WebhookSettings().validate_url(url, managed=True)
