"""Probe an installed release with real HTTP, PostgreSQL, CLI and hook retries."""

import asyncio
import json
import os
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

import httpx
from verify_worker_cancel_protocol import free_port


async def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rollback-cli", type=Path)
    args = parser.parse_args()
    cli = args.cli.resolve()
    assert version("graphharbor") == version("graphharbor-runtime") == "0.13.0.post48"
    assert "graphharbor_" in os.environ["DATABASE_URI"]
    with tempfile.TemporaryDirectory(prefix="graphharbor-retention-") as directory:
        root = Path(directory)
        (root / "graph.py").write_text(
            "from langgraph.graph import StateGraph, START, END\n"
            "builder = StateGraph(dict)\n"
            "builder.add_node('echo', lambda state: state)\n"
            "builder.add_edge(START, 'echo')\n"
            "builder.add_edge('echo', END)\n"
            "graph = builder.compile()\n"
        )
        (root / "hooks.py").write_text(
            "import os\nfrom pathlib import Path\n"
            "async def before(event):\n"
            "    return Path(os.environ['RETENTION_PROBE'], 'before').exists()\n"
            "async def after(event):\n"
            "    assert event['state'] == 'after_delete'\n"
            "    return Path(os.environ['RETENTION_PROBE'], 'after').exists()\n"
        )
        config = root / "langgraph.json"
        config.write_text(
            json.dumps(
                {
                    "dependencies": ["."],
                    "graphs": {"probe": "./graph.py:graph"},
                    "thread_lifecycle": {
                        "before_delete": "./hooks.py:before",
                        "after_delete": "./hooks.py:after",
                        "tombstone_metadata_keys": ["project_id"],
                    },
                }
            )
        )
        env = dict(os.environ, RETENTION_PROBE=str(root), LANGSMITH_TRACING="false")
        env.pop("PYTHONPATH", None)
        env.pop("LANGGRAPH_AUTH", None)

        async def command(*command):
            process = await asyncio.create_subprocess_exec(
                str(cli),
                *command,
                cwd=root,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
            assert process.returncode == 0, stderr.decode()[-3000:]
            return stdout.decode()

        await command("migrate")
        port = free_port()
        log = (root / "server.log").open("w")

        async def launch(executable=cli, *, expect_ready=True):
            process = await asyncio.create_subprocess_exec(
                str(executable),
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
                cwd=root,
                env=env,
                stdout=log,
                stderr=log,
            )
            if not expect_ready:
                await asyncio.wait_for(process.wait(), 45)
                assert process.returncode != 0
                assert "PostgreSQL schema contract is missing" in (root / "server.log").read_text()
                return process
            async with asyncio.timeout(45):
                while True:
                    assert process.returncode is None, (root / "server.log").read_text()[-3000:]
                    try:
                        async with httpx.AsyncClient() as probe:
                            if (await probe.get(f"http://127.0.0.1:{port}/ok")).is_success:
                                return process
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)

        async def stop(process):
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 15)
                except TimeoutError:
                    process.kill()
                    await process.wait()

        server = await launch()
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
                created = await client.post(
                    "/threads",
                    json={
                        "retention": {"mode": "idle", "ttl_minutes": 1},
                        "metadata": {"project_id": "probe", "title": "private probe"},
                    },
                )
                assert created.status_code in {200, 201}, created.text
                tid = created.json()["thread_id"]
                path = f"/threads/{tid}"
                initial = (await client.get(path + "/retention")).json()
                assert (await client.get(path + "/retention")).json() == initial
                touched = await client.post(path + "/touch")
                assert touched.json()["last_activity_at"] > initial["last_activity_at"]
                assert (
                    await client.patch(
                        path + "/retention",
                        json={
                            "policy": {"mode": "fixed", "ttl_minutes": 0.002},
                        },
                    )
                ).is_success
                await asyncio.sleep(0.2)
                report = await command("retention", "--config", str(config), "--limit", "1")
                assert tid in report and (await client.get(path)).is_success
                await command("retention", "--config", str(config), "--apply", "--limit", "1")
                assert (await client.get(path + "/deletion")).json()["state"] == "before_delete"
                assert (await client.post(path + "/touch")).status_code == 409
                rollback_cases = []
                if args.rollback_cli:
                    pending = (await client.get(path + "/deletion")).json()
                    await stop(server)
                    await launch(args.rollback_cli.resolve(), expect_ready=False)
                    server = await launch()
                    assert (await client.get(path + "/deletion")).json() == pending
                    assert (await client.get(path)).is_success
                    rollback_cases = [
                        "stopped-maintenance-preserves-task",
                        "old-schema-guard-rejects-live-downgrade",
                        "resume-matching-release",
                    ]
                (root / "before").touch()
                await asyncio.sleep(2.1)
                await command("retention", "--config", str(config), "--apply")
                assert (await client.get(path)).status_code == 404
                assert (await client.get(path + "/deletion")).json()["state"] == "after_delete"
                await stop(server)
                server = await launch()
                (root / "after").touch()
                await asyncio.sleep(4.1)
                await command("retention", "--config", str(config), "--apply")
                assert (await client.get(path + "/deletion")).json()["state"] == "completed"
                from uuid import UUID

                from langgraph_runtime_pg.database import connect, start_pool, stop_pool
                from langgraph_runtime_pg.models import ThreadDeletionRow

                await start_pool()
                try:
                    async with connect() as conn:
                        assert (await conn.session.get(ThreadDeletionRow, UUID(tid))).metadata_ == {
                            "project_id": "probe"
                        }
                finally:
                    await stop_pool()
                assert (await client.delete(path)).status_code == 204
                assert (await client.post("/threads", json={"thread_id": tid})).status_code == 409
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(
                        {
                            "version": version("graphharbor"),
                            "cases": [
                                "http-policy-touch",
                                "read-does-not-touch",
                                "cli-preview-read-only",
                                "before-hook-retry",
                                "deleting-fence",
                                "after-hook-retry",
                                "restart-resume",
                                "completed-tombstone",
                                "no-uuid-resurrection",
                                "completed-metadata-minimized",
                                *rollback_cases,
                            ],
                        },
                        indent=2,
                    )
                    + "\n"
                )
                sys.stdout.write(args.output.read_text())
        finally:
            await stop(server)
            log.close()


if __name__ == "__main__":
    asyncio.run(main())
