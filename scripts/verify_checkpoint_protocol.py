"""Compare checkpoint mutation behavior on isolated official and published servers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
from verify_worker_cancel_protocol import free_port

AUTH = Path(__file__).resolve().parents[1] / "tests/acceptance_app/boundary_auth.py"

GRAPH = """import asyncio
from typing import TypedDict
from langgraph.graph import START, END, StateGraph
class State(TypedDict, total=False):
    value: int
    block: bool
async def step(state):
    if state.get("block"):
        await asyncio.sleep(60)
    return {"value": state.get("value", 0)}
graph = StateGraph(State).add_node("step", step).add_edge(START,"step").add_edge("step",END).compile()
"""


async def probe(root: Path, executable: str, kind: str, jobs: int) -> list[dict]:
    directory = root / f"{kind}-{jobs}"
    directory.mkdir()
    (directory / "graph.py").write_text(GRAPH)
    (directory / "auth.py").write_text(AUTH.read_text())
    config = directory / "langgraph.json"
    config.write_text(
        json.dumps(
            {
                "dependencies": ["."],
                "graphs": {"probe": "./graph.py:graph"},
                "auth": {"path": "./auth.py:auth"},
            }
        )
    )
    env = dict(os.environ)
    for key in ("LANGSERVE_GRAPHS", "LANGGRAPH_AUTH", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"):
        env.pop(key, None)
    env.update(
        {
            "LANGSMITH_TRACING": "false",
            "LANGGRAPH_ALLOW_BLOCKING": "true",
            "LANGGRAPH_AUTH_TYPE": "custom",
            "GRAPHHARBOR_RUNTIME_CONTEXT_SECRET": "isolated-checkpoint-protocol-fixture",
        }
    )
    port = free_port()
    if kind == "official":
        env["LANGGRAPH_RUNTIME_EDITION"] = "inmem"
        args = [executable, "dev", "--no-reload", "--allow-blocking"]
    else:
        env["GRAPHHARBOR_ENV"] = "development"
        migration = await asyncio.create_subprocess_exec(
            executable,
            "migrate",
            cwd=directory,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(migration.communicate(), 60)
        except TimeoutError:
            migration.kill()
            await migration.wait()
            raise
        assert migration.returncode == 0, (stdout + stderr).decode()
        args = [executable, "serve"]
    args += [
        "--config",
        str(config),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--n-jobs-per-worker",
        str(jobs),
        "--no-browser",
    ]
    processes = []
    with (directory / "server.log").open("w") as log:
        try:
            processes.append(
                await asyncio.create_subprocess_exec(
                    *args, cwd=directory, env=env, stdout=log, stderr=log, start_new_session=True
                )
            )
            if kind == "graphharbor" and jobs:
                processes.append(
                    await asyncio.create_subprocess_exec(
                        executable,
                        "worker",
                        "--config",
                        str(config),
                        "--n-jobs-per-worker",
                        "1",
                        cwd=directory,
                        env=env,
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                    )
                )
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", headers={"authorization": "alice"}, timeout=20
            ) as client:
                deadline = time.monotonic() + 60
                while True:
                    assert all(p.returncode is None for p in processes), (
                        directory / "server.log"
                    ).read_text()[-4000:]
                    try:
                        if (await client.get("/ok")).is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    assert time.monotonic() < deadline, "readiness deadline"
                    await asyncio.sleep(0.1)
                cases = []

                async def capture(name, path, body, *, params=None, identity="alice"):
                    response = await client.post(
                        path, json=body, params=params, headers={"authorization": identity}
                    )
                    payload = (
                        response.json()
                        if response.content
                        and "application/json" in response.headers.get("content-type", "")
                        else response.text or None
                    )
                    cases.append(
                        {
                            "case": name,
                            "worker_jobs": jobs,
                            "status": response.status_code,
                            "body": payload,
                        }
                    )
                    assert response.status_code < 500, cases[-1]
                    return response

                async def thread(identity="alice"):
                    response = await client.post(
                        "/threads", json={}, headers={"authorization": identity}
                    )
                    response.raise_for_status()
                    return response.json()["thread_id"]

                async def run(tid, *, identity="alice", pending=False):
                    body = {"assistant_id": "probe", "input": {"value": 1, "block": bool(jobs)}}
                    if pending:
                        body["after_seconds"] = 3600
                    response = await client.post(
                        f"/threads/{tid}/runs", json=body, headers={"authorization": identity}
                    )
                    assert response.is_success, (
                        response.status_code,
                        response.text,
                        (directory / "server.log").read_text()[-6000:],
                    )
                    rid = response.json()["run_id"]
                    if jobs and not pending:
                        async with asyncio.timeout(20):
                            while (  # noqa: ASYNC110 - poll state in a separate server process
                                await client.get(
                                    f"/threads/{tid}/runs/{rid}",
                                    headers={"authorization": identity},
                                )
                            ).json()["status"] != "running":
                                await asyncio.sleep(0.05)
                    return rid

                for action in ("interrupt", "rollback"):
                    for wait in (False, True):
                        tid = await thread()
                        rid = await run(tid)
                        path = f"/threads/{tid}/runs/{rid}/cancel"
                        params = {"action": action, "wait": str(wait).lower()}
                        result = await capture(
                            f"{'running' if jobs else 'pending'}-{action}-{wait}",
                            path,
                            {},
                            params=params,
                        )
                        assert result.status_code == (200 if wait else 202)
                        await capture(f"repeat-{action}-{wait}", path, {}, params=params)
                own, other = await thread(), await thread("bob")
                own_run = await run(own, pending=True)
                other_run = await run(other, identity="bob", pending=True)
                foreign_path = f"/threads/{other}/runs/{other_run}"
                before = (await client.get(foreign_path, headers={"authorization": "bob"})).json()
                body = {"run_ids": [own_run, other_run, own_run, str(uuid4())], "status": "pending"}
                await capture(
                    "mixed-owner-batch", "/runs/cancel", body, params={"action": "rollback"}
                )
                await capture(
                    "repeat-mixed-owner-batch", "/runs/cancel", body, params={"action": "rollback"}
                )
                after = (await client.get(foreign_path, headers={"authorization": "bob"})).json()
                assert before == after, "mixed batch changed the other owner's run"
                denied = await capture(
                    "foreign-single-cancel",
                    foreign_path + "/cancel",
                    {},
                    params={"action": "rollback"},
                )
                assert denied.status_code in (403, 404)
                for name, body, params in [
                    ("invalid-run-id", {"run_ids": ["invalid"]}, None),
                    ("invalid-status", {"status": "invalid"}, None),
                    ("invalid-action", {"run_ids": []}, {"action": "invalid"}),
                ]:
                    response = await capture(name, "/runs/cancel", body, params=params)
                    assert response.status_code in (400, 422)
                for name, body in [
                    ("prune-invalid-id", {"thread_ids": ["invalid"]}),
                    ("prune-invalid-strategy", {"thread_ids": [], "strategy": "invalid"}),
                    ("prune-empty", {"thread_ids": []}),
                    (
                        "prune-keep-latest",
                        {"thread_ids": [await thread()], "strategy": "keep_latest"},
                    ),
                    (
                        "prune-mixed-owner",
                        {"thread_ids": [own, other, own, str(uuid4())], "strategy": "delete"},
                    ),
                    ("prune-repeat", {"thread_ids": [own], "strategy": "delete"}),
                ]:
                    await capture(name, "/threads/prune", body)
                assert (
                    await client.get(f"/threads/{other}", headers={"authorization": "bob"})
                ).status_code == 200
                assert (
                    await client.get(foreign_path, headers={"authorization": "bob"})
                ).json() == before
                cases.append(
                    {"case": "mixed-owner-resources-unchanged", "worker_jobs": jobs, "passed": True}
                )
                return cases
        finally:
            for process in reversed(processes):
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), 15)
                    except TimeoutError:
                        os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()


async def main(args):
    results = {}
    with tempfile.TemporaryDirectory(prefix="graphharbor-checkpoint-protocol-") as directory:
        for kind, executable in (("official", args.official), ("graphharbor", args.graphharbor)):
            results[kind] = []
            for jobs in (0, 1):
                results[kind] += await probe(Path(directory), str(executable), kind, jobs)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.write(json.dumps({kind: len(cases) for kind, cases in results.items()}) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official", type=Path, required=True)
    parser.add_argument("--graphharbor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.official = args.official.resolve()
    args.graphharbor = args.graphharbor.resolve()

    async def bounded_main():
        async with asyncio.timeout(300):
            await main(args)

    asyncio.run(bounded_main())
