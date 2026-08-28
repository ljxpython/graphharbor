from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from langhost.mcp_transport import create_mcp_transport


class _Graph:
    async def ainvoke(self, value: dict[str, Any], *, config: Any, version: str) -> dict[str, Any]:
        assert version == "v2"
        return {"value": int(value.get("value", 0)) + 1}


class _Registry:
    def ids(self) -> tuple[str, ...]:
        return ("basic",)

    def get(self, graph_id: str) -> _Graph:
        assert graph_id == "basic"
        return _Graph()


@pytest.mark.asyncio
async def test_streamable_http_discovers_and_calls_graph_tool() -> None:
    _server, app = create_mcp_transport(_Registry())
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client,
    ):
        headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        }
        init = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert init.status_code == 200
        tools = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert tools.status_code == 200 and '"name":"basic"' in tools.text
        call = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "basic", "arguments": {"input": {"value": 1}}},
            },
        )
        assert call.status_code == 200
        assert '\\"value\\": 2' in call.text

        rejected = await client.post(
            "/",
            headers={**headers, "host": "attacker.example"},
            json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
        )
        assert rejected.status_code in {400, 421}

        invalid = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "basic", "arguments": {}},
            },
        )
        assert invalid.status_code == 200 and '"isError":true' in invalid.text


def test_transport_helper_is_importable_without_server_lifespan() -> None:
    server, _ = create_mcp_transport(_Registry())
    tools = asyncio.run(server.list_tools())
    assert json.loads(json.dumps(tools[0].inputSchema))["required"] == ["input"]
