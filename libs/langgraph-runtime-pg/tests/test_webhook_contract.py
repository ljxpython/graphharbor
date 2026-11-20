from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from uuid import uuid4

import pytest


def test_webhook_config_expands_only_allowed_environment(monkeypatch) -> None:
    from langgraph_runtime_pg.webhooks import configure_webhooks

    monkeypatch.setenv("LG_WEBHOOK_TOKEN", "secret")
    value = configure_webhooks(
        {
            "webhooks": {
                "headers": {"Authorization": "Bearer ${{ env.LG_WEBHOOK_TOKEN }}"},
                "allowed_fields": ["run_id", "status"],
            }
        }
    )
    assert value.headers == {"Authorization": "Bearer secret"}
    assert value.allowed_fields == frozenset({"run_id", "status"})

    with pytest.raises(ValueError, match="unavailable"):
        configure_webhooks({"webhooks": {"headers": {"X": "${{ env.NOT_ALLOWED }}"}}})


def test_webhook_url_policy_blocks_private_and_userinfo() -> None:
    from langgraph_runtime_pg.webhooks import WebhookSettings

    policy = WebhookSettings(url_policy={"disable_loopback": True, "disable_private_ips": True})
    with pytest.raises(ValueError):
        policy.validate_url("http://user:pass@example.com/hook")
    with pytest.raises(ValueError):
        policy.validate_ip("127.0.0.1")
    with pytest.raises(ValueError):
        policy.validate_ip("10.0.0.2")


def test_terminal_signature_uses_raw_body_and_fixed_path(monkeypatch) -> None:
    from langgraph_runtime_pg.webhook_delivery import signed_headers
    from langgraph_runtime_pg.webhooks import configure_webhooks

    secret = "s" * 32
    monkeypatch.setenv("GRAPHHARBOR_TERMINAL_WEBHOOK_KEY_ID", "runtime-a")
    monkeypatch.setenv("GRAPHHARBOR_TERMINAL_WEBHOOK_SECRET", secret)
    configure_webhooks(
        {
            "webhooks": {
                "terminal": {
                    "enabled": True,
                    "url": "https://api.example.test/api/runtime/internal/run-completion",
                }
            }
        }
    )
    event_id = uuid4()
    row = SimpleNamespace(
        event_id=event_id,
        target="https://api.example.test/api/runtime/internal/run-completion",
        body='{"status":"error"}',
    )
    headers = signed_headers(row, timestamp=1_700_000_000)
    raw = (
        "runtime-a\n1700000000\nPOST\n/api/runtime/internal/run-completion\n" + row.body
    ).encode()
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    assert headers["X-Run-Completion-Signature"] == "sha256=" + expected
    assert headers["X-Run-Completion-Event-Id"] == str(event_id)


def test_managed_envelope_excludes_raw_error_and_private_context() -> None:
    from langgraph_runtime_pg.webhooks import public_kwargs, safe_projection

    assert public_kwargs({"input": {"x": 1}, "runtime_context_token": "secret"}) == {
        "input": {"x": 1}
    }
    assert safe_projection({"reason_code": "runtime.model.retry_exhausted"}) == {
        "reason_code": "runtime.model.retry_exhausted"
    }
    assert safe_projection({"x": object()}) == {}
