"""Exercise cold-package rollback, mixed versions and real Worker kills in isolation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import httpx
from verify_worker_cancel_protocol import GRAPH, free_port, marker

from langgraph_runtime_pg.checkpoint_mutations import rollback_receipt
from langgraph_runtime_pg.database import connect, start_pool, stop_pool
from langgraph_runtime_pg.models import RunRow
from langgraph_runtime_pg.run_store import stopped_event


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert "graphharbor_" in os.environ["DATABASE_URI"]
    evidence = []
    with tempfile.TemporaryDirectory(prefix="graphharbor-cancel-release-") as directory:
        root = Path(directory)
        (root / "graph.py").write_text(GRAPH)
        config = root / "langgraph.json"
        config.write_text(
            json.dumps({"dependencies": ["."], "graphs": {"cancel": "./graph.py:graph"}})
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "PYTHONPATH",
                "LANGSMITH_API_KEY",
                "LANGCHAIN_API_KEY",
                "LANGGRAPH_AUTH",
                "LANGSERVE_GRAPHS",
            }
        }
        env.update(
            GRAPHHARBOR_ENV="development",
            LANGSMITH_TRACING="false",
            LANGCHAIN_TRACING_V2="false",
            GRAPHHARBOR_RUN_WAIT_TIMEOUT="0.4",
            GRAPHHARBOR_LEASE_SECONDS="5",
            LG_BG_JOB_HEARTBEAT="2",
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

        async def stop(process, *, kill=False):
            if process.returncode is None:
                process.kill() if kill else process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 15)
                except TimeoutError:
                    process.kill()
                    await process.wait()

        async def serve(executable):
            port = free_port()
            server = await launch(
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
                    assert server.returncode is None, (
                        root / f"process-{processes.index(server)}.log"
                    ).read_text()[-5000:]
                    try:
                        if (await client.get("/ok")).is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
            return server, client

        async def create(client, label, *, thread_id=None):
            control = root / label
            control.mkdir()
            assistant = (await client.post("/assistants", json={"graph_id": "cancel"})).json()
            if thread_id is None:
                thread_id = (await client.post("/threads", json={})).json()["thread_id"]
            response = await client.post(
                f"/threads/{thread_id}/runs",
                json={
                    "assistant_id": assistant["assistant_id"],
                    "input": {"marker_directory": str(control)},
                    "multitask_strategy": "enqueue",
                },
            )
            response.raise_for_status()
            return thread_id, response.json()["run_id"], control

        async def worker(executable):
            return await launch(
                executable, "worker", "--config", str(config), "--n-jobs-per-worker", "1"
            )

        async def await_released(run_id):
            async with asyncio.timeout(10):
                while True:
                    async with connect() as conn:
                        row = await conn.session.get(RunRow, UUID(run_id))
                        if row is None or row.lease_owner is None:
                            return
                    await asyncio.sleep(0.05)

        migration = await launch(args.candidate, "migrate")
        await migration.wait()
        assert migration.returncode == 0
        await start_pool()
        try:
            server, client = await serve(args.candidate)
            try:
                for action in ("interrupt", "rollback"):
                    tid, rid, control = await create(client, f"drain-{action}")
                    executing = await worker(args.candidate)
                    await marker(control / "entered")
                    _, pending, next_control = await create(client, f"next-{action}", thread_id=tid)
                    path = f"/threads/{tid}/runs/{rid}/cancel?action={action}"
                    assert (await client.post(path)).status_code == 202
                    await marker(control / "cleaning")
                    executing.terminate()
                    async with connect() as conn:
                        row = await conn.session.get(RunRow, UUID(rid))
                        queued = await conn.session.get(RunRow, UUID(pending))
                        assert row.lease_owner and queued.status == "pending"
                        assert not (next_control / "entered").exists()
                    (control / "release").touch()
                    assert (await client.post(path + "&wait=true")).status_code == 200
                    await asyncio.wait_for(executing.wait(), 15)
                    replacement = await worker(args.legacy)
                    await marker(next_control / "entered")
                    await client.post(f"/threads/{tid}/runs/{pending}/cancel")
                    await marker(next_control / "cleaning")
                    (next_control / "release").touch()
                    await await_released(pending)
                    await stop(replacement)
                    evidence.append(
                        {
                            "case": "drain-before-post41-rollback",
                            "action": action,
                            "pending_waited_for_cleanup": True,
                            "post42_stop_confirmed": True,
                            "post41_resumed_pending": True,
                            "schema_migration": False,
                        }
                    )

                for action in ("interrupt", "rollback"):
                    for stage in ("before-cancel", "during-cleanup", "after-confirmation"):
                        tid, rid, control = await create(client, f"kill-{action}-{stage}")
                        executing = await worker(args.candidate)
                        await marker(control / "entered")
                        path = f"/threads/{tid}/runs/{rid}/cancel?action={action}"
                        if stage == "before-cancel":
                            await stop(executing, kill=True)
                        assert (await client.post(path)).status_code == 202
                        if stage != "before-cancel":
                            await marker(control / "cleaning")
                        if stage == "after-confirmation":
                            (control / "release").touch()
                            assert (await client.post(path + "&wait=true")).status_code == 200
                            await marker(control / "stopped")
                        if stage != "before-cancel":
                            await stop(executing, kill=True)
                        if stage != "after-confirmation":
                            await asyncio.sleep(5.1)
                        recovery = await worker(args.candidate)
                        response = await client.post(path + "&wait=true")
                        if stage == "after-confirmation" and action == "rollback":
                            assert response.status_code == 404
                        else:
                            assert response.status_code == (
                                200 if stage == "after-confirmation" else 503
                            ), response.text
                        await await_released(rid)
                        async with connect() as conn:
                            row = await conn.session.get(RunRow, UUID(rid))
                            if action == "rollback":
                                receipt = await rollback_receipt(conn.session, UUID(rid))
                                assert receipt.payload["execution_stopped"] is (
                                    stage == "after-confirmation"
                                )
                            else:
                                assert row.status == "interrupted" and row.lease_owner is None
                                assert (await stopped_event(conn.session, row) is not None) is (
                                    stage == "after-confirmation"
                                )
                        await stop(recovery)
                        assert (control / "starts").read_text().splitlines() == ["start"]
                        evidence.append(
                            {
                                "case": "real-worker-kill",
                                "action": action,
                                "stage": stage,
                                "wait_status": response.status_code,
                                "stopped_confirmed": stage == "after-confirmation",
                                "cancelled_run_reexecuted": False,
                            }
                        )
            finally:
                await client.aclose()
                await stop(server)

            for api, execution, label in (
                (args.candidate, args.legacy, "new-api-old-worker"),
                (args.legacy, args.candidate, "old-api-new-worker"),
            ):
                server, client = await serve(api)
                try:
                    for action in ("interrupt", "rollback"):
                        tid, rid, control = await create(client, f"mixed-{label}-{action}")
                        executing = await worker(execution)
                        await marker(control / "entered")
                        waiting = asyncio.create_task(
                            client.post(
                                f"/threads/{tid}/runs/{rid}/cancel?action={action}&wait=true"
                            )
                        )
                        await marker(control / "cleaning")
                        assert not (control / "stopped").exists()
                        legacy_rollback = api == args.legacy and action == "rollback"
                        if legacy_rollback:
                            assert not waiting.done()
                            before_cleanup = None
                        else:
                            response = await waiting
                            assert response.status_code == (
                                503 if api == args.candidate else 200
                            ), response.text
                            before_cleanup = response.status_code
                        (control / "release").touch()
                        await marker(control / "stopped")
                        if legacy_rollback:
                            assert (await waiting).status_code == 200
                        await await_released(rid)
                        await stop(executing)
                        evidence.append(
                            {
                                "case": "mixed-versions",
                                "combination": label,
                                "action": action,
                                "wait_status_before_cleanup": before_cleanup,
                                "legacy_rollback_waited_for_run_deletion": legacy_rollback,
                                "new_stop_guarantee": False,
                            }
                        )
                finally:
                    await client.aclose()
                    await stop(server)
        except BaseException:
            for index in range(len(logs)):
                sys.stderr.write((root / f"process-{index}.log").read_text()[-1000:])
            raise
        finally:
            for cleaning in await asyncio.to_thread(lambda: list(root.glob("*/cleaning"))):
                (cleaning.parent / "release").touch()
            for process in reversed(processes):
                if process.returncode is None:
                    await stop(process, kill=True)
            await stop_pool()
            for log in logs:
                log.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2) + "\n")
        sys.stdout.write(json.dumps({"release_scenarios": len(evidence), "passed": True}) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
