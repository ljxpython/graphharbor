"""Isolated real HTTP/SDK comparison with pinned langgraph dev and GraphHarbor.

Run through run_isolated_tests.py; the PostgreSQL/Redis URIs must be disposable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

import httpx
from langgraph_sdk import get_client

JS_CANCEL = Path(__file__).resolve().parents[1] / "tests/javascript/cancel.mjs"

GRAPH = """import asyncio
from pathlib import Path
from typing import TypedDict
from langgraph.graph import START, END, StateGraph
class State(TypedDict):
    marker_directory: str
async def block(state):
    directory = Path(state["marker_directory"])
    (directory / "entered").touch()
    with (directory / "starts").open("a") as starts:
        starts.write("start\\n")
    try:
        await asyncio.Event().wait()
    finally:
        (directory / "cleaning").touch()
        while not (directory / "release").exists():
            await asyncio.sleep(0.01)
        (directory / "stopped").touch()
    return state
graph = StateGraph(State).add_node("block", block).add_edge(START, "block").add_edge("block", END).compile()
"""


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def marker(path, timeout=15):
    async with asyncio.timeout(timeout):
        while not path.exists():  # noqa: ASYNC110 - file barrier shared with a separate process
            await asyncio.sleep(0.01)


async def probe(root, executable, kind, jobs):
    directory = root / f"{kind}-{jobs}"
    directory.mkdir()
    (directory / "graph.py").write_text(GRAPH)
    config = directory / "langgraph.json"
    config.write_text(json.dumps({"dependencies": ["."], "graphs": {"cancel": "./graph.py:graph"}}))
    port = free_port()
    env = dict(os.environ)
    for name in ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "LANGSERVE_GRAPHS", "LANGGRAPH_AUTH"):
        env.pop(name, None)
    env.update(
        {
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "LANGGRAPH_ALLOW_BLOCKING": "true",
            "GRAPHHARBOR_ENV": "development",
        }
    )
    args = [
        executable,
        "dev" if kind == "official" else "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--config",
        str(config),
        "--no-browser",
        "--n-jobs-per-worker",
        str(jobs),
    ]
    if kind == "official":
        args += ["--no-reload", "--allow-blocking"]
        env["LANGGRAPH_RUNTIME_EDITION"] = "inmem"
    else:
        migration = await asyncio.create_subprocess_exec(
            executable,
            "migrate",
            cwd=directory,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await migration.communicate()
        assert migration.returncode == 0, (stdout + stderr).decode()
    with (
        (directory / "server.log").open("w") as log,
        (directory / "worker.log").open("w") as worker_log,
    ):
        process = await asyncio.create_subprocess_exec(
            *args, cwd=directory, env=env, stdout=log, stderr=log
        )
        processes = [process]
        if kind == "graphharbor" and jobs:
            processes.append(
                await asyncio.create_subprocess_exec(
                    executable,
                    "worker",
                    "--config",
                    str(config),
                    "--n-jobs-per-worker",
                    str(jobs),
                    cwd=directory,
                    env=env,
                    stdout=worker_log,
                    stderr=worker_log,
                )
            )
        cases = []
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as http:
                async with asyncio.timeout(90):
                    while True:
                        if process.returncode is not None:
                            raise AssertionError((directory / "server.log").read_text()[-10000:])
                        try:
                            if (await http.get("/ok")).is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.1)
                sdk = get_client(url=f"http://127.0.0.1:{port}")
                try:
                    assistant = await sdk.assistants.create(graph_id="cancel")
                    for action in ("interrupt", "rollback"):
                        for wait in (False, True):
                            control = directory / f"{action}-{wait}"
                            control.mkdir()
                            thread = await sdk.threads.create()
                            tid = thread["thread_id"]
                            run = await sdk.runs.create(
                                tid,
                                assistant["assistant_id"],
                                input={"marker_directory": str(control)},
                                stream_resumable=True,
                            )
                            rid = run["run_id"]
                            if jobs:
                                await marker(control / "entered")
                            started = time.monotonic()
                            request = asyncio.create_task(
                                http.post(
                                    f"/threads/{tid}/runs/{rid}/cancel",
                                    params={"wait": str(wait).lower(), "action": action},
                                )
                            )
                            if jobs:
                                await marker(control / "cleaning")
                                await asyncio.sleep(0.1)
                                before_cleanup = request.done()
                                if kind == "graphharbor" and wait:
                                    assert not before_cleanup, "wait returned before node cleanup"
                                (control / "release").touch()
                            else:
                                before_cleanup = None
                            response = await asyncio.wait_for(request, 20)
                            if jobs:
                                await marker(control / "stopped")
                            body = response.json() if response.content else None
                            if isinstance(body, dict) and "marker_directory" in body:
                                body = {**body, "marker_directory": "<fixture-path>"}
                            cases.append(
                                {
                                    "state": "running" if jobs else "pending",
                                    "action": action,
                                    "wait": wait,
                                    "status": response.status_code,
                                    "body": body,
                                    "headers": {
                                        key: response.headers.get(key)
                                        for key in ("content-type", "location", "content-location")
                                    },
                                    "response_before_cleanup": before_cleanup,
                                    "elapsed_seconds": round(time.monotonic() - started, 3),
                                }
                            )
                            assert response.status_code == (200 if wait else 202), cases[-1]
                    for action in ("interrupt", "rollback"):
                        for wait in (False, True):
                            control = directory / f"python-{action}-{wait}"
                            control.mkdir()
                            thread = await sdk.threads.create()
                            run = await sdk.runs.create(
                                thread["thread_id"],
                                assistant["assistant_id"],
                                input={"marker_directory": str(control)},
                            )
                            if jobs:
                                await marker(control / "entered")
                            request = asyncio.create_task(
                                sdk.runs.cancel(
                                    thread["thread_id"],
                                    run["run_id"],
                                    wait=wait,
                                    action=action,
                                )
                            )
                            if jobs:
                                await marker(control / "cleaning")
                                await asyncio.sleep(0.1)
                                if wait:
                                    assert not request.done()
                                (control / "release").touch()
                            await asyncio.wait_for(request, 20)
                            if jobs:
                                await marker(control / "stopped")
                            cases.append(
                                {
                                    "sdk": f"python-{version('langgraph-sdk')}",
                                    "state": "running" if jobs else "pending",
                                    "action": action,
                                    "wait": wait,
                                    "passed": True,
                                }
                            )
                    javascript = await asyncio.create_subprocess_exec(
                        "node",
                        str(JS_CANCEL),
                        f"http://127.0.0.1:{port}",
                        assistant["assistant_id"],
                        str(directory),
                        "running" if jobs else "pending",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    processes.append(javascript)
                    stdout, stderr = await asyncio.wait_for(javascript.communicate(), 90)
                    assert javascript.returncode == 0, stderr.decode()
                    cases.extend(json.loads(stdout))
                finally:
                    await sdk.aclose()
            return cases
        finally:
            for cleaning in directory.glob("*/cleaning"):
                (cleaning.parent / "release").touch()
            for child in reversed(processes):
                if child.returncode is None:
                    child.terminate()
                    try:
                        await asyncio.wait_for(child.wait(), 15)
                    except TimeoutError:
                        child.kill()
                        await child.wait()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--graphharbor", default=str(Path(sys.executable).parent / "graphharbor"))
    parser.add_argument("--official", default=str(Path(sys.executable).parent / "langgraph"))
    parser.add_argument("--kind", choices=["both", "graphharbor"], default="both")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="graphharbor-cancel-http-") as directory:
        results = {}
        for kind, executable in (("official", args.official), ("graphharbor", args.graphharbor)):
            if args.kind != "both" and kind != args.kind:
                continue
            results[kind] = []
            for jobs in (0, 1):
                results[kind] += await probe(Path(directory), executable, kind, jobs)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(results, indent=2) + "\n")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2) + "\n")
        sys.stdout.write(json.dumps({kind: len(cases) for kind, cases in results.items()}) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
