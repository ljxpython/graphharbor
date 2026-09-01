"""Real-process worker crash and checkpoint takeover acceptance gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import psycopg

from langgraph_runtime_pg.database import to_psycopg_uri

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "tests" / "acceptance_app" / "langgraph.json"
DEFAULT_RESULT = ROOT / "artifacts" / "fault-injection-result.json"


async def _wait_for(
    client: httpx.AsyncClient,
    path: str,
    predicate,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            response = await client.get(path)
            response.raise_for_status()
            last = response.json()
            if predicate(last):
                return last
        except httpx.HTTPError as exc:
            last = {"error": f"{type(exc).__name__}: {exc}"}
        await asyncio.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {path}: {last}")


async def _start(command: list[str], env: dict[str, str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *command,
        cwd=ROOT,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )


async def _stop(process: asyncio.subprocess.Process | None, sig: signal.Signals) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


def _database_evidence(database_uri: str, run_id: str) -> dict[str, Any]:
    with psycopg.connect(to_psycopg_uri(database_uri)) as connection:
        run = connection.execute(
            "SELECT status, retry_count FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
        events = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE terminal) AS terminal_total
            FROM runtime_events WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()
        shutdown_requeues = connection.execute(
            """
            SELECT COUNT(*)
            FROM runtime_events
            WHERE run_id = %s AND payload->>'reason' = 'shutdown_requeue'
            """,
            (run_id,),
        ).fetchone()
    if run is None or events is None or shutdown_requeues is None:
        raise AssertionError(f"run {run_id} disappeared during fault injection")
    return {
        "status": run[0],
        "retry_count": run[1],
        "event_total": events[0],
        "terminal_event_total": events[1],
        "shutdown_requeue_total": shutdown_requeues[0],
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    prefix = args.prefix or f"graphharbor:fault:{int(time.time())}"
    env = {
        **os.environ,
        "DATABASE_URI": args.database_uri,
        "REDIS_URI": args.redis_uri,
        "GRAPHHARBOR_REDIS_PREFIX": prefix,
        "GRAPHHARBOR_ENV": "development",
        "LG_RUNTIME_PG_AUTO_MIGRATE": "false",
        "GRAPHHARBOR_REAPER_INTERVAL_SECONDS": "1",
        "GRAPHHARBOR_LEASE_SECONDS": str(args.lease_seconds),
    }
    api: asyncio.subprocess.Process | None = None
    worker: asyncio.subprocess.Process | None = None
    replacement: asyncio.subprocess.Process | None = None
    base_url = f"http://127.0.0.1:{args.port}"
    result: dict[str, Any] = {
        "prefix": prefix,
        "lease_seconds": args.lease_seconds,
        "worker_signal": args.worker_signal,
        "worker_killed": False,
        "worker_stopped": False,
    }
    try:
        api = await _start(
            [
                sys.executable,
                "-m",
                "langhost.cli",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
                "--config",
                str(args.config),
                "--n-jobs-per-worker",
                "0",
            ],
            env,
        )
        async with httpx.AsyncClient(base_url=base_url, timeout=10, trust_env=False) as client:
            await _wait_for(client, "/ready", lambda value: value.get("ready") is True, 60)
            assistant = (
                await client.post(
                    "/assistants", json={"graph_id": "slow_recovery", "name": "fault"}
                )
            ).json()
            thread = (await client.post("/threads", json={"graph_id": "slow_recovery"})).json()
            run = (
                await client.post(
                    f"/threads/{thread['thread_id']}/runs",
                    json={"assistant_id": assistant["assistant_id"], "input": {}},
                )
            ).json()
            run_id = str(run["run_id"])
            result.update({"run_id": run_id, "thread_id": thread["thread_id"]})

        worker = await _start(
            [
                sys.executable,
                "-m",
                "langhost.cli",
                "worker",
                "--config",
                str(args.config),
                "--n-jobs-per-worker",
                "1",
            ],
            env,
        )
        async with httpx.AsyncClient(base_url=base_url, timeout=10, trust_env=False) as client:
            await _wait_for(
                client,
                f"/threads/{thread['thread_id']}/runs/{run_id}",
                lambda value: value.get("status") == "running",
                30,
            )
            await _wait_for(
                client,
                f"/threads/{thread['thread_id']}/state",
                lambda value: (value.get("values") or {}).get("marker") == "checkpointed",
                30,
            )

        worker_signal = getattr(signal, args.worker_signal)
        await _stop(worker, worker_signal)
        worker = None
        result["worker_killed"] = worker_signal == signal.SIGKILL
        result["worker_stopped"] = True
        replacement = await _start(
            [
                sys.executable,
                "-m",
                "langhost.cli",
                "worker",
                "--config",
                str(args.config),
                "--n-jobs-per-worker",
                "1",
            ],
            env,
        )
        async with httpx.AsyncClient(base_url=base_url, timeout=10, trust_env=False) as client:
            final = await _wait_for(
                client,
                f"/threads/{thread['thread_id']}/runs/{run_id}",
                lambda value: value.get("status") in {"success", "error", "interrupted"},
                args.timeout,
            )
            state = await client.get(f"/threads/{thread['thread_id']}/state")
            state.raise_for_status()
        result["api_status"] = final["status"]
        result["state"] = state.json().get("values")
        result["database"] = _database_evidence(args.database_uri, run_id)
        if result["api_status"] != "success":
            raise AssertionError(f"replacement worker ended run as {result['api_status']}")
        if result["state"] != {"marker": "checkpointed", "completed": True}:
            raise AssertionError(f"unexpected recovered state: {result['state']!r}")
        if result["database"]["terminal_event_total"] != 1:
            raise AssertionError(f"expected one terminal event: {result['database']}")
        if worker_signal == signal.SIGTERM and result["database"]["shutdown_requeue_total"] < 1:
            raise AssertionError(f"missing graceful shutdown requeue: {result['database']}")
        return result
    finally:
        await _stop(replacement, signal.SIGTERM)
        await _stop(worker, signal.SIGTERM)
        await _stop(api, signal.SIGTERM)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-uri", default=os.environ.get("DATABASE_URI"))
    parser.add_argument("--redis-uri", default=os.environ.get("REDIS_URI"))
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--port", type=int, default=31298)
    parser.add_argument("--lease-seconds", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--worker-signal", choices=("SIGKILL", "SIGTERM"), default="SIGKILL")
    parser.add_argument("--result-out", type=Path, default=DEFAULT_RESULT)
    args = parser.parse_args()
    if not args.database_uri or not args.redis_uri:
        parser.error("--database-uri and --redis-uri are required")
    try:
        result = asyncio.run(_run(args))
    except Exception as exc:
        result = {"status": "failed", "failure": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        return 1
    result["status"] = "passed"
    args.result_out.parent.mkdir(parents=True, exist_ok=True)
    args.result_out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
