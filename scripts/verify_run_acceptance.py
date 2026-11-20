"""Real API/Worker receipt recovery; run only with disposable PostgreSQL/Redis."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import psycopg
from verify_worker_cancel_protocol import free_port

SECRET = "synthetic-run-acceptance-harness-secret"
GRAPH = """from pathlib import Path
from langgraph.graph import START, END, StateGraph
def step(state):
    with Path(state['marker']).open('a') as output:
        output.write('start\\n')
    return {'value': 2}
graph = StateGraph(dict).add_node('step', step).add_edge(START, 'step').add_edge('step', END).compile()
"""
AUTH = """import os
import jwt
from langgraph_sdk import Auth
auth = Auth()
@auth.authenticate
async def authenticate(authorization):
    data = jwt.decode(authorization.removeprefix('Bearer '), os.environ['RECEIPT_TEST_SECRET'], algorithms=['HS256'])
    return {'identity': 'fixture', **data}
@auth.on
async def authorize(ctx, value):
    return {}
if os.environ.get('RECEIPT_FAIL_ENQUEUE'):
    from langhost import core_api
    async def fail_enqueue(run_id):
        raise RuntimeError('synthetic Redis enqueue failure')
    core_api.enqueue_run = fail_enqueue
"""


def headers(thread=None, body=None, key="key", operation="create"):
    claims = {}
    if thread is not None:
        claims["run_acceptance"] = {
            "scope_id": "a" * 64,
            "credential_id": "b" * 64,
            "operation": operation,
            "thread_id": thread,
            "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
            "request_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
        }
    result = {"Authorization": "Bearer " + jwt.encode(claims, SECRET, algorithm="HS256")}
    if thread is not None:
        result.update(
            {
                "Content-Type": "application/json",
                "Idempotency-Key": key,
                "GraphHarbor-Request-Digest": claims["run_acceptance"]["request_digest"],
            }
        )
    return result


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate", type=Path, default=Path(sys.executable).parent / "graphharbor"
    )
    parser.add_argument("--legacy", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    uri = os.environ["POSTGRES_URI"]
    assert "/graphharbor_event_retention_verify" in uri
    evidence = {
        "started_at": datetime.now(UTC).isoformat(),
        "command": sys.argv,
        "python_sdk": version("langgraph-sdk"),
        "installations": [],
        "processes": [],
        "cases": [],
        "passed": False,
    }
    for executable in (args.candidate, args.legacy):
        if executable is None:
            continue
        process = await asyncio.create_subprocess_exec(
            str(executable.parent / "python"),
            "-c",
            "import json; from importlib.metadata import version; "
            "import langhost, langgraph_runtime_pg; "
            "from langgraph_runtime_pg.migrate import head_revision; "
            "print(json.dumps({'graphharbor':version('graphharbor'),"
            "'runtime':version('graphharbor-runtime'),'sdk':version('langgraph-sdk'),"
            "'gateway_source':langhost.__file__,'runtime_source':langgraph_runtime_pg.__file__,"
            "'head':head_revision()}))",
            stdout=asyncio.subprocess.PIPE,
        )
        output, _ = await asyncio.wait_for(process.communicate(), 90)
        assert process.returncode == 0
        evidence["installations"].append(json.loads(output))
    with tempfile.TemporaryDirectory(prefix="graphharbor-receipts-") as directory:
        root = Path(directory)
        (root / "graph.py").write_text(GRAPH)
        (root / "auth.py").write_text(AUTH)
        config = root / "langgraph.json"
        config.write_text(
            json.dumps(
                {
                    "dependencies": ["."],
                    "graphs": {"probe": "./graph.py:graph"},
                    "auth": {"path": "./auth.py:auth"},
                }
            )
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"PYTHONPATH", "LANGSERVE_GRAPHS", "LANGGRAPH_AUTH"}
        }
        env.update(
            NO_PROXY="127.0.0.1,localhost",
            GRAPHHARBOR_ENV="development",
            LG_RUNTIME_PG_AUTO_MIGRATE="false",
            LANGSMITH_TRACING="false",
            LANGCHAIN_TRACING_V2="false",
            RECEIPT_TEST_SECRET=SECRET,
            GRAPHHARBOR_RUNTIME_CONTEXT_SECRET=SECRET,
        )
        processes, logs, clients = [], [], []

        async def launch(executable, *command, fail_enqueue=False):
            path = root / f"process-{len(processes)}.log"
            log = path.open("w")
            logs.append(log)
            process = await asyncio.create_subprocess_exec(
                str(executable),
                *command,
                cwd=root,
                env={**env, **({"RECEIPT_FAIL_ENQUEUE": "1"} if fail_enqueue else {})},
                stdout=log,
                stderr=log,
            )
            processes.append(process)
            evidence["processes"].append(
                {"pid": process.pid, "executable": str(executable), "role": command[0]}
            )
            return process

        async def stop(process):
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 15)
                except TimeoutError:
                    process.kill()
                    await process.wait()

        async def migrate(executable):
            process = await launch(executable, "migrate")
            await asyncio.wait_for(process.wait(), 180)
            assert process.returncode == 0, (
                root / f"process-{processes.index(process)}.log"
            ).read_text()[-3000:]

        async def serve(executable, *, fail_enqueue=False):
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
                fail_enqueue=fail_enqueue,
            )
            client = httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", headers=headers(), timeout=30, trust_env=False
            )
            clients.append(client)
            try:
                async with asyncio.timeout(180):
                    while True:
                        assert process.returncode is None, (
                            root / f"process-{processes.index(process)}.log"
                        ).read_text()[-3000:]
                        try:
                            response = await client.get("/ready")
                            if response.is_success:
                                return client, process, port
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.1)
            except TimeoutError:
                print((root / f"process-{processes.index(process)}.log").read_text()[-3000:])  # noqa: T201
                raise

        def sql(statement, values=()):
            with psycopg.connect(uri) as conn:
                return conn.execute(statement, values).fetchall()

        async def thread(client):
            response = await client.post("/threads", json={})
            response.raise_for_status()
            return response.json()["thread_id"]

        async def post(client, tid, body, key):
            return await client.post(
                f"/threads/{tid}/runs/acceptance", content=body, headers=headers(tid, body, key)
            )

        async def read(client, tid, body, key):
            response = await client.get(
                f"/threads/{tid}/runs/acceptance", headers=headers(tid, body, key, "read")
            )
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            return response.json()

        def payload(marker, **changes):
            return json.dumps(
                {
                    "assistant_id": "probe",
                    "input": {"marker": str(marker)},
                    "multitask_strategy": "enqueue",
                    **changes,
                },
                separators=(",", ":"),
            ).encode()

        def record(case, **facts):
            evidence["cases"].append(
                {
                    "case": case,
                    "observed_at": datetime.now(UTC).isoformat(),
                    "schema_head": sql("SELECT version_num FROM alembic_version")[0][0],
                    "run_count": sql("SELECT count(*) FROM runs")[0][0],
                    "ledger_count": sql("SELECT count(*) FROM run_acceptance_receipts")[0][0],
                    **facts,
                }
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, indent=2) + "\n")

        async def wait_run(rid, status):
            async with asyncio.timeout(90):
                while sql("SELECT status FROM runs WHERE run_id=%s", (rid,)) != [(status,)]:  # noqa: ASYNC110 - database barrier with a separate Worker
                    await asyncio.sleep(0.1)

        try:
            await migrate(args.candidate)
            api1, process1, port1 = await serve(args.candidate)
            api2, process2, _ = await serve(args.candidate)
            tid = await thread(api1)
            body = payload(root / "starts")
            assert (await read(api1, tid, body, "concurrent"))["result"] == "unknown"
            responses = await asyncio.gather(
                *(post(api1 if i % 2 else api2, tid, body, "concurrent") for i in range(12))
            )
            assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
            ids = {r.json()["run_id"] for r in responses}
            assert len(ids) == 1
            rid = ids.pop()
            assert sql("SELECT count(*) FROM runs WHERE run_id=%s", (rid,)) == [(1,)]
            before = sql("SELECT row_to_json(runs)::text FROM runs WHERE run_id=%s", (rid,))
            for _ in range(20):
                assert (await read(api2, tid, body, "concurrent"))["run_id"] == rid
            assert sql("SELECT row_to_json(runs)::text FROM runs WHERE run_id=%s", (rid,)) == before
            assert not (root / "starts").exists()
            assert (await post(api1, tid, body + b" ", "concurrent")).status_code == 409
            other = await thread(api1)
            assert (await post(api2, other, body, "concurrent")).status_code == 409
            record(
                "two-api-concurrency-pending-read",
                run_id=rid,
                receipts=1,
                runs=1,
                execution_starts=0,
                reads=20,
            )

            busy_body = payload(root / "busy", multitask_strategy="reject")
            denied = (await post(api1, tid, busy_body, "busy")).json()
            assert denied["result"] == "definitively_not_accepted"
            response = await api1.post(f"/threads/{tid}/runs/{rid}/cancel", params={"wait": "true"})
            assert response.status_code == 200, response.text
            await wait_run(rid, "interrupted")
            assert (await post(api2, tid, busy_body, "busy")).json()[
                "result"
            ] == "definitively_not_accepted"
            record("queued-stop-and-permanent-busy-rejection", run_id=rid, execution_starts=0)

            # Send bytes and deliberately never read the HTTP response before killing the API.
            lost_tid = await thread(api1)
            lost_body = payload(root / "lost-starts")
            reader, writer = await asyncio.open_connection("127.0.0.1", port1)
            raw_headers = headers(lost_tid, lost_body, "lost")
            request = (
                f"POST /threads/{lost_tid}/runs/acceptance HTTP/1.1\r\nHost: localhost\r\nContent-Length: {len(lost_body)}\r\n"
                + "".join(f"{k}: {v}\r\n" for k, v in raw_headers.items())
                + "\r\n"
            )
            writer.write(request.encode() + lost_body)
            await writer.drain()
            async with asyncio.timeout(30):
                while not sql(  # noqa: ASYNC110 - committed transaction barrier with a separate API
                    "SELECT run_id FROM run_acceptance_receipts WHERE thread_id=%s", (lost_tid,)
                ):
                    await asyncio.sleep(0.01)
            process1.kill()
            await process1.wait()
            writer.close()
            await writer.wait_closed()
            del reader
            lost = await read(api2, lost_tid, lost_body, "lost")
            assert lost["result"] == "accepted"
            api1, process1, _ = await serve(args.candidate)
            assert (await read(api1, lost_tid, lost_body, "lost"))["run_id"] == lost["run_id"]
            worker = await launch(
                args.candidate, "worker", "--config", str(config), "--n-jobs-per-worker", "1"
            )
            await wait_run(lost["run_id"], "success")
            assert (root / "lost-starts").read_text() == "start\n"
            await stop(worker)
            record(
                "lost-ack-api-kill-restart-worker-recovery",
                run_id=lost["run_id"],
                execution_starts=1,
            )

            broken, broken_process, _ = await serve(args.candidate, fail_enqueue=True)
            broken_tid = await thread(broken)
            broken_body = payload(root / "redis-starts")
            accepted = (await post(broken, broken_tid, broken_body, "redis")).json()
            assert accepted["result"] == "accepted"
            await stop(broken_process)
            worker = await launch(
                args.candidate, "worker", "--config", str(config), "--n-jobs-per-worker", "1"
            )
            await wait_run(accepted["run_id"], "success")
            await stop(worker)
            assert (root / "redis-starts").read_text() == "start\n"
            record(
                "after-commit-enqueue-failure-pg-recovery",
                run_id=accepted["run_id"],
                execution_starts=1,
            )

            sql("DELETE FROM runs WHERE run_id=%s RETURNING run_id", (lost["run_id"],))
            sql("DELETE FROM threads WHERE thread_id=%s RETURNING thread_id", (lost_tid,))
            deleted = await read(api2, lost_tid, lost_body, "lost")
            assert (
                deleted["run_id"] == lost["run_id"] and deleted["run_state"]["available"] is False
            )
            assert (await post(api1, lost_tid, lost_body, "lost")).json()["run_id"] == lost[
                "run_id"
            ]
            sql(
                "UPDATE run_acceptance_receipts SET closed_observed_at=now()-interval '8 days',details_expires_at=now()-interval '1 day' WHERE thread_id=%s RETURNING receipt_key",
                (lost_tid,),
            )
            assert (await read(api2, lost_tid, lost_body, "lost"))["reason"] == "receipt_expired"
            assert (await post(api1, lost_tid, lost_body, "lost")).status_code == 409
            record("deletion-expiry-permanent-key", run_id=lost["run_id"], execution_starts=1)

            if args.legacy:
                await stop(process1)
                await stop(process2)
                retained = sql(
                    "SELECT receipt_key,request_digest,outcome FROM run_acceptance_receipts ORDER BY receipt_key"
                )
                # Code rollback keeps additive tables; the schema marker is matched explicitly.
                sql(
                    "UPDATE alembic_version SET version_num='012_run_callback_deliveries' RETURNING version_num"
                )
                legacy_api, legacy_process, _ = await serve(args.legacy)
                new_post = await post(
                    legacy_api, str(uuid4()), payload(root / "legacy-reject"), "legacy"
                )
                assert new_post.status_code in {404, 405}, new_post.text
                record(
                    "legacy-api-new-post-refused", status=new_post.status_code, execution_starts=0
                )
                mixed_tid = await thread(legacy_api)
                await stop(legacy_process)
                sql(
                    "UPDATE alembic_version SET version_num='013_run_acceptance_receipts' RETURNING version_num"
                )
                mixed_api, mixed_process, _ = await serve(args.candidate)
                mixed_body = payload(root / "mixed-starts")
                mixed = (await post(mixed_api, mixed_tid, mixed_body, "mixed")).json()
                await stop(mixed_process)
                sql(
                    "UPDATE alembic_version SET version_num='012_run_callback_deliveries' RETURNING version_num"
                )
                legacy_worker = await launch(
                    args.legacy, "worker", "--config", str(config), "--n-jobs-per-worker", "1"
                )
                sql(
                    "UPDATE alembic_version SET version_num='013_run_acceptance_receipts' RETURNING version_num"
                )
                recovered_api, recovered_process, _ = await serve(args.candidate)
                await wait_run(mixed["run_id"], "success")
                assert (root / "mixed-starts").read_text() == "start\n"
                assert (await read(recovered_api, mixed_tid, mixed_body, "mixed"))[
                    "run_id"
                ] == mixed["run_id"]
                await stop(legacy_worker)
                assert set(retained) <= set(
                    sql("SELECT receipt_key,request_digest,outcome FROM run_acceptance_receipts")
                )
                assert (await read(recovered_api, lost_tid, lost_body, "lost"))[
                    "reason"
                ] == "receipt_expired"
                await stop(recovered_process)
                record(
                    "new-api-legacy-worker-code-rollback-upgrade",
                    run_id=mixed["run_id"],
                    ledger_preserved=True,
                    execution_starts=1,
                )
            evidence.update(passed=True, finished_at=datetime.now(UTC).isoformat())
            args.output.write_text(json.dumps(evidence, indent=2) + "\n")
            print(json.dumps({"cases": len(evidence["cases"]), "passed": True}))  # noqa: T201
        finally:
            for client in clients:
                await client.aclose()
            for process in reversed(processes):
                await stop(process)
            for log in logs:
                log.close()


if __name__ == "__main__":
    asyncio.run(main())
