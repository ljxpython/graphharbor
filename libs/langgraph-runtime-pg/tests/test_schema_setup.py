from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_schema_setup_callback_uses_the_locked_connection(monkeypatch) -> None:
    from langgraph_runtime_pg import schema_setup

    events: list[str] = []

    class FakeConnection:
        async def __aenter__(self):
            events.append("open")
            return self

        async def __aexit__(self, *_args):
            events.append("close")

        async def execute(self, query, _params):
            events.append("lock" if "advisory_lock" in query else "unlock")

    async def connect(*_args, **_kwargs):
        return FakeConnection()

    monkeypatch.setattr(schema_setup.AsyncConnection, "connect", connect)

    async def setup(connection) -> None:
        assert isinstance(connection, FakeConnection)
        events.append("setup")

    await schema_setup.run_schema_setup("postgresql://unused", setup)

    assert events == ["open", "lock", "setup", "unlock", "close"]
