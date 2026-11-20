"""Verify cold packages and an additive, data-preserving rollback in isolation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
import psycopg
from sqlalchemy import select
from verify_worker_cancel_protocol import free_port

from langgraph_runtime_pg.database import connect, start_pool, stop_pool, to_psycopg_uri
from langgraph_runtime_pg.models import RunCallbackDeliveryRow as Delivery
from langgraph_runtime_pg.webhook_delivery import deliver_once
from langgraph_runtime_pg.webhooks import configure_webhooks

GRAPH = """from pathlib import Path
from langgraph.graph import START, END, StateGraph
def step(state):
    with Path(state['marker']).open('a') as output:
        output.write('start\\n')
    return {'value': state['value'] + 1}
graph = StateGraph(dict).add_node('step', step).add_edge(START, 'step').add_edge('step', END).compile()
"""


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    uri = to_psycopg_uri(os.environ["DATABASE_URI"])
    assert "/graphharbor_event_retention_verify" in uri
    evidence = []
    with tempfile.TemporaryDirectory(prefix="graphharbor-webhook-release-") as directory:
        root = Path(directory)
        (root / "graph.py").write_text(GRAPH)
        config = root / "langgraph.json"
        config.write_text(
            json.dumps({"dependencies": ["."], "graphs": {"probe": "./graph.py:graph"}})
        )
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        env.update(
            GRAPHHARBOR_ENV="development",
            LG_RUNTIME_PG_AUTO_MIGRATE="false",
            LANGSMITH_TRACING="false",
            LANGCHAIN_TRACING_V2="false",
        )
        processes = []
        logs = []

        async def launch(executable, *command):
            log = (root / f"process-{len(processes)}.log").open("w")
            logs.append(log)
            process = await asyncio.create_subprocess_exec(
                str(executable), *command, cwd=root, env=env, stdout=log, stderr=log
            )
            processes.append(process)
            return process

        async def stop_all():
            for process in reversed(processes):
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 15)
                    except TimeoutError:
                        process.kill()
                        await process.wait()

        async def migrate(executable):
            process = await launch(executable, "migrate")
            await asyncio.wait_for(process.wait(), 60)
            assert process.returncode == 0

        async def serve(executable):
            port = free_port()
            process = await launch(
                executable,
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--config",
                str(config),
                "--n-jobs-per-worker",
                "0",
                "--no-browser",
            )
            client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=15)
            async with asyncio.timeout(60):
                while True:
                    assert process.returncode is None, (
                        root / f"process-{processes.index(process)}.log"
                    ).read_text()[-4000:]
                    try:
                        if (await client.get("/ok")).is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
            response = await client.get("/ready")
            assert response.status_code == 200, response.text
            return client

        async def reject_mismatched_schema(executable):
            process = await launch(
                executable,
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(free_port()),
                "--config",
                str(config),
                "--n-jobs-per-worker",
                "0",
                "--no-browser",
            )
            await asyncio.wait_for(process.wait(), 60)
            assert process.returncode != 0
            assert (
                "PostgreSQL schema contract is missing"
                in (root / f"process-{processes.index(process)}.log").read_text()
            )

        async def run(executable, label, *, webhook=False):
            client = await serve(executable)
            try:
                await launch(
                    executable, "worker", "--config", str(config), "--n-jobs-per-worker", "1"
                )
                response = await client.post("/threads", json={})
                response.raise_for_status()
                thread = response.json()["thread_id"]
                payload = {
                    "assistant_id": "probe",
                    "input": {"value": 1, "marker": str(root / label)},
                    **({"webhook": "https://callback.invalid/hook"} if webhook else {}),
                }
                response = await client.post(f"/threads/{thread}/runs", json=payload)
                response.raise_for_status()
                run_id = response.json()["run_id"]
                async with asyncio.timeout(60):
                    while True:
                        response = await client.get(f"/threads/{thread}/runs/{run_id}")
                        response.raise_for_status()
                        if response.json()["status"] == "success":
                            break
                        await asyncio.sleep(0.1)
                assert (root / label).read_text() == "start\n"
                evidence.append({"case": label, "run_id": run_id, "status": "success", "starts": 1})
                return run_id
            finally:
                await client.aclose()
                await stop_all()

        try:
            await migrate(args.legacy)
            await run(args.legacy, "official-post43-baseline")
            await migrate(args.candidate)
            retained_run = await run(args.candidate, "candidate-before-rollback", webhook=True)
            with psycopg.connect(uri) as conn:
                before = conn.execute(
                    "SELECT event_id, body FROM run_callback_deliveries WHERE run_id=%s",
                    (retained_run,),
                ).fetchone()
                assert before is not None
            await reject_mismatched_schema(args.legacy)
            await stop_all()
            with psycopg.connect(uri) as conn:
                assert conn.execute(
                    "UPDATE alembic_version SET version_num='011_run_cancellations' "
                    "WHERE version_num='012_run_callback_deliveries' RETURNING version_num"
                ).fetchone()
            await reject_mismatched_schema(args.candidate)
            await stop_all()
            await run(args.legacy, "official-post43-after-rollback")
            with psycopg.connect(uri) as conn:
                assert (
                    conn.execute(
                        "SELECT event_id, body FROM run_callback_deliveries WHERE run_id=%s",
                        (retained_run,),
                    ).fetchone()
                    == before
                )
            await migrate(args.candidate)
            await start_pool()
            configure_webhooks({})
            async with connect() as conn:
                delivery = await conn.session.scalar(
                    select(Delivery).where(Delivery.run_id == retained_run)
                )
                assert delivery.event_id == before[0] and delivery.body == before[1]
                from datetime import UTC, datetime

                delivery.state = "pending"
                delivery.next_attempt_at = datetime.now(UTC)

            async def ack(row):
                assert row.event_id == before[0] and row.body == before[1]
                return 204, b""

            assert await deliver_once("release-recovery", ack)
            async with connect() as conn:
                assert (await conn.session.get(Delivery, delivery.delivery_id)).state == "delivered"
            await stop_pool()
            assert (root / "candidate-before-rollback").read_text() == "start\n"
            await run(args.candidate, "candidate-after-upgrade")
            evidence.append(
                {
                    "case": "preserved-outbox-recovery",
                    "event_id": str(before[0]),
                    "body_preserved": True,
                    "delivered": True,
                    "agent_reexecuted": False,
                    "mismatched_schema_startup": "refused",
                }
            )
            args.output.write_text(json.dumps(evidence, indent=2))
            sys.stdout.write(json.dumps(evidence) + "\n")
        finally:
            await stop_pool()
            await stop_all()
            for log in logs:
                log.close()


if __name__ == "__main__":
    asyncio.run(main())
