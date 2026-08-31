"""Fast checks for the complex acceptance fixtures (no server or provider)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import p0_graphs
from p0_graphs import assistant_supervisor, network_sse
from run_network_sse import _validate_remote_url


def test_supervisor_map_reduce_fixture() -> None:
    result = assistant_supervisor.invoke({"request": "acceptance"})
    assert len(result["findings"]) == 2
    assert "billing" in result["summary"] and "technical" in result["summary"]


def test_network_fixture_has_three_phases() -> None:
    result = asyncio.run(network_sse.ainvoke({"phases": []}))
    assert result["phases"] == ["phase-complete"] * 3


def test_cross_network_sse_rejects_loopback_urls() -> None:
    for value in ("http://localhost:31296", "http://127.0.0.1:31296", "http://[::1]:31296"):
        try:
            _validate_remote_url(value)
        except ValueError:
            continue
        raise AssertionError(f"loopback URL was accepted: {value}")


def test_cross_network_sse_accepts_remote_url() -> None:
    assert (
        _validate_remote_url("https://acceptance.example.test") == "https://acceptance.example.test"
    )


@pytest.mark.asyncio
async def test_deepagent_factory_scopes_backend_and_subagent(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(p0_graphs, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setenv("GRAPHHARBOR_WORKSPACE_ROOT", str(tmp_path))
    result = await p0_graphs.deepagent_demo(
        {
            "configurable": {
                "thread_id": "thread-1",
                "__graphharbor_runtime_context": {
                    "tenant_id": "tenant-1",
                    "project_id": "project-1",
                },
            }
        }
    )

    assert result is not None
    backend = captured["backend"]
    assert backend.cwd == tmp_path / "tenant-1" / "project-1" / "thread-1"
    assert captured["skills"] == ["/skills/project/guardrails/"]
    assert len(captured["permissions"]) == 1
    assert captured["subagents"][0]["tools"]
    assert captured["subagents"][0]["skills"] == []
    assert captured["subagents"][0]["permissions"][0].mode == "deny"
    assert captured["subagents"][1]["name"] == "general-purpose"
    assert captured["subagents"][1]["permissions"][0].mode == "deny"
