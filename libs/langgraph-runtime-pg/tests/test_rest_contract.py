"""Direct REST contract coverage for the public Core Agent Server surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


def _graph_config(root: Path) -> dict[str, dict[str, str]]:
    (root / "graphs.py").write_text(
        "from typing import TypedDict\n"
        "from langgraph.graph import END, START, StateGraph\n"
        "class State(TypedDict):\n"
        "    value: int\n"
        "def increment(state: State):\n"
        "    return {'value': state['value'] + 1}\n"
        "builder = StateGraph(State)\n"
        "builder.add_node('increment', increment)\n"
        "builder.add_edge(START, 'increment')\n"
        "builder.add_edge('increment', END)\n"
        "graph = builder.compile()\n",
        encoding="utf-8",
    )
    return {"assistant": {"path": "graphs.py:graph"}}


@pytest.mark.asyncio
async def test_core_rest_contract_covers_resources_and_errors(pg_runtime, tmp_path: Path) -> None:
    from langhost.server import create_app

    app = create_app({"graphs": _graph_config(tmp_path)}, base_dir=tmp_path)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        for path in ("/ok", "/live", "/info", "/openapi.json", "/metrics"):
            response = await client.get(path)
            assert response.status_code == 200, (path, response.text)

        openapi = (await client.get("/openapi.json")).json()
        for path, methods in {
            "/assistants/search": {"post"},
            "/assistants/count": {"post"},
            "/threads/count": {"post"},
            "/threads/{thread_id}/copy": {"post"},
            "/runs/batch": {"post"},
            "/runs/cancel": {"post"},
            "/runs/crons": {"post"},
            "/runs/crons/search": {"post"},
            "/runs/crons/count": {"post"},
            "/threads/{thread_id}/commands": {"post"},
            "/threads/{thread_id}/stream/events": {"post"},
            "/threads/{thread_id}/stream": {"get"},
            "/store/items": {"get", "put", "delete"},
            "/store/items/search": {"post"},
            "/store/namespaces": {"post"},
        }.items():
            assert path in openapi["paths"]
            assert methods <= set(openapi["paths"][path])

        assistant_response = await client.post(
            "/assistants", json={"graph_id": "assistant", "name": "rest-contract"}
        )
        assert assistant_response.status_code == 200, assistant_response.text
        assistant = assistant_response.json()
        assistant_id = assistant["assistant_id"]

        namespace = ["rest", "contract"]
        assert (
            await client.put(
                "/store/items", json={"namespace": namespace, "key": "item", "value": {"n": 1}}
            )
        ).status_code == 204
        item = await client.get("/store/items", params={"namespace": "rest.contract", "key": "item"})
        assert item.status_code == 200
        assert item.json()["namespace"] == namespace
        assert item.json()["value"] == {"n": 1}
        search = await client.post("/store/items/search", json={"namespace_prefix": ["rest"]})
        assert search.status_code == 200
        assert search.json()["items"][0]["key"] == "item"
        namespaces = await client.post("/store/namespaces", json={"prefix": ["rest"]})
        assert namespaces.json() == {"namespaces": [namespace]}
        assert (
            await client.request(
                "DELETE", "/store/items", json={"namespace": namespace, "key": "item"}
            )
        ).status_code == 204
        assert (
            await client.get("/store/items", params={"namespace": "rest.contract", "key": "item"})
        ).json() is None

        assert (await client.get(f"/assistants/{assistant_id}")).status_code == 200
        assert (
            await client.post("/assistants/search", json={"graph_id": "assistant"})
        ).status_code == 200
        assert (await client.post("/assistants/count", json={"graph_id": "assistant"})).json() == 1
        assert (await client.get(f"/assistants/{assistant_id}/graph")).status_code == 200
        assert (await client.get(f"/assistants/{assistant_id}/schemas")).status_code == 200
        assert (await client.get(f"/assistants/{assistant_id}/subgraphs")).status_code == 200
        assert (
            await client.post(f"/assistants/{assistant_id}/versions", json={})
        ).status_code == 200
        assert (
            await client.post(f"/assistants/{assistant_id}/latest", json={"version": 1})
        ).status_code == 200
        assert (
            await client.patch(f"/assistants/{assistant_id}", json={"metadata": {"checked": True}})
        ).status_code == 200

        thread_response = await client.post(
            "/threads", json={"graph_id": "assistant", "metadata": {"suite": "rest"}}
        )
        assert thread_response.status_code == 200
        thread_id = thread_response.json()["thread_id"]
        assert (await client.get(f"/threads/{thread_id}")).status_code == 200
        assert (await client.get("/threads")).status_code == 200
        assert (
            await client.post("/threads/search", json={"metadata": {"suite": "rest"}})
        ).status_code == 200
        assert (
            await client.post("/threads/count", json={"metadata": {"suite": "rest"}})
        ).json() == 1
        assert (await client.get(f"/threads/{thread_id}/state")).status_code == 200
        assert (
            await client.post(f"/threads/{thread_id}/state", json={"values": {"value": 1}})
        ).status_code == 200
        assert (
            await client.post(f"/threads/{thread_id}/history", json={"limit": 5})
        ).status_code == 200
        copied_response = await client.post(f"/threads/{thread_id}/copy")
        assert copied_response.status_code == 201
        copied_thread_id = copied_response.json()["thread_id"]
        assert copied_thread_id != thread_id

        run_response = await client.post(
            f"/threads/{thread_id}/runs",
            json={"assistant_id": assistant_id, "input": {"value": 1}},
        )
        assert run_response.status_code == 201, run_response.text
        run_id = run_response.json()["run_id"]
        assert (await client.get(f"/threads/{thread_id}/runs")).status_code == 200
        assert (await client.get(f"/threads/{thread_id}/runs/{run_id}")).status_code == 200
        assert (await client.post("/runs/batch", json=[])).status_code == 201
        assert (await client.post("/runs/cancel", json={"run_ids": [run_id]})).status_code == 200
        assert (await client.get(f"/threads/{thread_id}/runs/{run_id}")).json()[
            "status"
        ] == "interrupted"
        assert (await client.post(f"/threads/{thread_id}/runs/{run_id}/cancel")).status_code == 200
        assert (await client.get(f"/threads/{thread_id}/runs/{run_id}/join")).status_code == 200
        assert (await client.delete(f"/threads/{thread_id}/runs/{run_id}")).status_code == 204

        cron_response = await client.post(
            "/runs/crons",
            json={"assistant_id": assistant_id, "schedule": "* * * * *", "input": {"value": 1}},
        )
        assert cron_response.status_code == 200
        cron_id = cron_response.json()["cron_id"]
        assert (
            await client.post("/runs/crons/search", json={"assistant_id": assistant_id})
        ).status_code == 200
        assert (
            await client.post("/runs/crons/count", json={"assistant_id": assistant_id})
        ).json() == 1
        assert (
            await client.patch(f"/runs/crons/{cron_id}", json={"enabled": False})
        ).status_code == 200
        assert (
            await client.post(
                f"/threads/{thread_id}/runs/crons",
                json={"assistant_id": assistant_id, "schedule": "*/5 * * * *"},
            )
        ).status_code == 200
        assert (await client.delete(f"/runs/crons/{cron_id}")).status_code == 204

        assert (
            await client.post("/threads/prune", json={"thread_ids": [thread_id, copied_thread_id]})
        ).status_code == 200
        assert (await client.delete(f"/threads/{copied_thread_id}")).status_code == 204
        assert (await client.delete(f"/threads/{thread_id}")).status_code == 204
        assert (await client.delete(f"/assistants/{assistant_id}")).status_code == 204

        metrics = await client.get("/metrics")
        assert metrics.status_code == 200
        assert "graphharbor_postgres_pool_size" in metrics.text
        assert "graphharbor_redis_connected" in metrics.text
        assert "graphharbor_runs_created_total" in metrics.text

        assert (await client.get("/store/items")).status_code == 422
        assert (await client.get("/assistants/not-a-uuid")).status_code == 404
