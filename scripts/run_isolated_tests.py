"""Run a command with disposable native PostgreSQL/Redis, never the repository .env."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-name",
        default="graphharbor_event_retention_verify",
        choices=["graphharbor_event_retention_verify", "graphharbor_cron_probe_20261005"],
    )
    parser.add_argument("--redis-prefix")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if not command:
        raise SystemExit("usage: run_isolated_tests.py COMMAND [ARGS...]")
    pg_port, redis_port = free_port(), free_port()
    with tempfile.TemporaryDirectory(prefix="graphharbor-cancel-") as directory:
        root = Path(directory)
        data = root / "pg"
        subprocess.run(
            [
                "initdb",
                "-D",
                str(data),
                "-U",
                "postgres",
                "-A",
                "trust",
                "--no-locale",
                "-E",
                "UTF8",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        with (root / "services.log").open("w") as log:
            processes = []
            try:
                processes.append(
                    subprocess.Popen(
                        [
                            "postgres",
                            "-D",
                            str(data),
                            "-p",
                            str(pg_port),
                            "-h",
                            "127.0.0.1",
                            "-k",
                            str(root),
                            "-c",
                            "fsync=off",
                        ],
                        stdout=log,
                        stderr=log,
                    )
                )
                processes.append(
                    subprocess.Popen(
                        [
                            "redis-server",
                            "--port",
                            str(redis_port),
                            "--bind",
                            "127.0.0.1",
                            "--save",
                            "",
                            "--appendonly",
                            "no",
                            "--dir",
                            str(root),
                        ],
                        stdout=log,
                        stderr=log,
                    )
                )
                deadline = time.monotonic() + 30
                while True:
                    ready = (
                        subprocess.run(
                            [
                                "pg_isready",
                                "-h",
                                "127.0.0.1",
                                "-p",
                                str(pg_port),
                                "-U",
                                "postgres",
                            ],
                            stdout=subprocess.DEVNULL,
                        ).returncode
                        == 0
                    )
                    if ready:
                        break
                    if any(p.poll() is not None for p in processes) or time.monotonic() > deadline:
                        raise RuntimeError(
                            "isolated services failed: " + (root / "services.log").read_text()
                        )
                    time.sleep(0.1)
                subprocess.run(
                    [
                        "createdb",
                        "-h",
                        "127.0.0.1",
                        "-p",
                        str(pg_port),
                        "-U",
                        "postgres",
                        args.database_name,
                    ],
                    check=True,
                )
                env = dict(os.environ)
                env.update(
                    {
                        "DATABASE_URI": f"postgresql+asyncpg://postgres@127.0.0.1:{pg_port}/{args.database_name}",
                        "POSTGRES_URI": f"postgresql://postgres@127.0.0.1:{pg_port}/{args.database_name}",
                        "REDIS_URI": f"redis://127.0.0.1:{redis_port}/0",
                        "GRAPHHARBOR_REDIS_PREFIX": args.redis_prefix
                        or f"graphharbor:cancel:{pg_port}",
                        "LANGGRAPH_INTEGRATION_URL": f"http://127.0.0.1:{free_port()}",
                    }
                )
                return subprocess.run(command, env=env).returncode
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
