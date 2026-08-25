from pathlib import Path
from typing import Any

import httpx
import pytest

from langhost import cli as cli_module


def test_banner_uses_resolved_port() -> None:
    rendered = cli_module._langhost_welcome(
        host="127.0.0.1",
        port=51234,
        ssl=False,
        studio_origin=None,
        mount_prefix=None,
    )

    assert "http://127.0.0.1:51234" in rendered
    assert "31296" not in rendered


def test_serve_passes_resolved_port_to_banner_and_server(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_module, "validate_config_file", lambda _config: {})

    def resolve_port(host: str, port: int) -> int:
        calls["resolved"] = (host, port)
        return 51234

    def welcome(**kwargs: Any) -> str:
        calls["welcome"] = kwargs
        return "welcome"

    def run_server(host: str, port: int, *_args: Any, **_kwargs: Any) -> None:
        calls["server"] = (host, port)

    monkeypatch.setattr(cli_module, "_resolve_port", resolve_port)
    monkeypatch.setattr(cli_module, "_langhost_welcome", welcome)
    monkeypatch.setattr(cli_module, "run_server", run_server)

    cli_module.serve.callback(
        host="127.0.0.1",
        port=31296,
        config=tmp_path / "langgraph.json",
        env_file=None,
        database_uri="postgresql://example",
        redis_uri="redis://example",
        reload=False,
        reload_includes=(),
        reload_excludes=(),
        workers=1,
        n_jobs_per_worker=None,
        browser=False,
        studio_url=None,
        tunnel=False,
        debug_port=None,
        wait_for_client=False,
        allow_blocking=False,
        server_log_level="INFO",
        ssl_certfile=None,
        ssl_keyfile=None,
    )

    assert calls["resolved"] == ("127.0.0.1", 31296)
    assert calls["welcome"]["port"] == 51234
    assert calls["server"] == ("127.0.0.1", 51234)


@pytest.mark.asyncio
async def test_owned_server_exposes_public_health_and_capabilities() -> None:
    from langhost.server import create_app

    app = create_app({"graphs": {}})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.get("/ok")
        ready = await client.get("/ready")
        info = await client.get("/info")
        schema = await client.get("/openapi.json")
        metrics = await client.get("/metrics")
        stream = await client.post("/runs/stream", json={})
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    assert ready.status_code == 503 and ready.json()["ready"] is False
    assert info.status_code == 200 and info.json()["protocol"] == "langgraph-agent-server"
    capabilities = {item["name"]: item for item in info.json()["capabilities"]}
    assert capabilities["stream_v2"]["available"] is True
    assert capabilities["events_v2"]["available"] is True
    assert capabilities["events_v3"]["available"] is True
    assert schema.status_code == 200 and schema.json()["openapi"] == "3.1.0"
    assert metrics.status_code == 200 and "text/plain" in metrics.headers["content-type"]
    assert stream.status_code == 422 and stream.json()["detail"] == "assistant_id is required"


def test_owned_server_source_has_no_private_api_startup_import() -> None:
    source = (Path(__file__).parents[1] / "src" / "langhost" / "server.py").read_text()
    cli_source = (Path(__file__).parents[1] / "src" / "langhost" / "cli.py").read_text()
    assert "langgraph_api" not in source
    assert "langgraph_api" not in cli_source
