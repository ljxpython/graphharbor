"""Native SDK webhook entry points share the durable terminal sender."""

import asyncio
import json
import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from langgraph_sdk._async.client import LangGraphClient
from sqlalchemy import select

from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.models import CronRow, RunCallbackDeliveryRow as Delivery
from langgraph_runtime_pg.production_worker import ProductionWorker
from langgraph_runtime_pg.webhook_delivery import deliver_once
from langgraph_runtime_pg.webhooks import configure_webhooks
from langhost.server import create_app

JS_CWD = str(Path(__file__).resolve().parents[3] / "tests/javascript")


@pytest.mark.parametrize("sdk", ["python", "javascript"])
async def test_native_webhook_entry_points(pg_runtime, tmp_path, sdk):
    (tmp_path / "graph.py").write_text(
        "from langgraph.graph import START, END, StateGraph\n"
        "def step(state):\n    return {'value': state['value'] + 1}\n"
        "builder = StateGraph(dict)\n"
        "builder.add_node('step', step)\n"
        "builder.add_edge(START, 'step')\n"
        "builder.add_edge('step', END)\n"
        "graph = builder.compile()\n"
    )
    app = create_app(
        {"graphs": {"probe": "graph.py:graph"}, "http": {"disable_mcp": True}},
        base_dir=tmp_path,
    )
    app.state.webhook_dispatcher_enabled = False
    target = "https://callback.test/hook"
    async with (
        LifespanManager(app),
        LangGraphClient(
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        ) as client,
    ):
        worker = ProductionWorker(app.state.graph_registry, owner="webhook-sdk")

        async def work():
            while True:
                if not await worker.run_once():
                    await asyncio.sleep(0.02)

        task = asyncio.create_task(work())
        server = None
        server_task = None
        try:
            if sdk == "javascript":
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
                    server = uvicorn.Server(
                        uvicorn.Config(app, log_level="critical", lifespan="off")
                    )
                    server_task = asyncio.create_task(server.serve(sockets=[listener.dup()]))
                    await asyncio.sleep(0.1)
                    assert server.started
                    process = await asyncio.create_subprocess_exec(
                        "node",
                        "webhook.mjs",
                        cwd=JS_CWD,
                        env={**os.environ, "GRAPHHARBOR_URL": f"http://127.0.0.1:{port}"},
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
                    assert process.returncode == 0, stderr.decode()
                    cron = {"cron_id": json.loads(stdout)["cron_id"]}
            else:
                cron = await exercise_python(client, target)
            async with connect() as conn:
                row = await conn.session.get(CronRow, cron["cron_id"])
                row.next_run_date = datetime.now(UTC) - timedelta(seconds=1)
            async with asyncio.timeout(30):
                while True:
                    async with connect() as conn:
                        rows = (await conn.session.scalars(select(Delivery))).all()
                    if len(rows) == 6:
                        break
                    await asyncio.sleep(0.05)
            assert all(row.profile == "standard" and row.target == target for row in rows)
            bodies = [json.loads(row.body) for row in rows]
            assert all(value["status"] == "success" for value in bodies)
            assert any(value["values"]["value"] == 8 for value in bodies)
            assert all("runtime_context_token" not in row.body for row in rows)

            async def standard_ack(row):
                return 204, b""

            for _ in rows:
                assert await deliver_once("sdk-sender", standard_ack)
            assert not await deliver_once("sdk-sender", standard_ack)
            await client.crons.delete(cron["cron_id"])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if server is not None:
                server.should_exit = True
                await server_task
            configure_webhooks({})


async def exercise_python(client, target):
    thread = await client.threads.create()
    created = await client.runs.create(
        thread["thread_id"], "probe", input={"value": 1}, webhook=target
    )
    assert (await client.runs.join(thread["thread_id"], created["run_id"]))["value"] == 2
    assert (await client.runs.wait(None, "probe", input={"value": 2}, webhook=target))["value"] == 3
    streamed = [
        part
        async for part in client.runs.stream(
            thread["thread_id"],
            "probe",
            input={"value": 3},
            webhook=target,
        )
    ]
    assert streamed
    batch = await client.runs.create_batch(
        [
            {"assistant_id": "probe", "input": {"value": 4}, "webhook": target},
            {"assistant_id": "probe", "input": {"value": 5}, "webhook": target},
        ]
    )
    assert len(batch) == 2
    cron = await client.crons.create(
        "probe", schedule="0 0 1 1 *", input={"value": 6}, webhook=target
    )
    await client.crons.update(cron["cron_id"], webhook=target, input={"value": 7})
    return cron
