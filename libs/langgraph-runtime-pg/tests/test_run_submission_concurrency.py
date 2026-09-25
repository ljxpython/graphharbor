import asyncio
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_reject_serializes_concurrent_submissions_and_replays_key(pg_runtime):
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RunRow, ThreadRow
    from langgraph_runtime_pg.run_store import RunConflictError, RunRepository

    assistant_id, thread_id, other_thread = uuid4(), uuid4(), uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="test",
                name="test",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        conn.session.add_all([ThreadRow(thread_id=thread_id), ThreadRow(thread_id=other_thread)])

    async def submit(key, target=thread_id):
        async with connect() as conn:
            run = await RunRepository().create(
                conn.session,
                assistant_id=assistant_id,
                thread_id=target,
                kwargs={"input": {"text": "hello"}},
                metadata={},
                idempotency_key=key,
                multitask_strategy="reject",
            )
            return run.run_id

    outcomes = await asyncio.gather(submit("one"), submit("two"), return_exceptions=True)
    assert sum(isinstance(item, RunConflictError) for item in outcomes) == 1
    winner_index = next(i for i, item in enumerate(outcomes) if not isinstance(item, Exception))
    winner_key = ("one", "two")[winner_index]
    assert await submit(winner_key) == outcomes[winner_index]
    with pytest.raises(RunConflictError):
        await submit(winner_key, other_thread)
    async with connect() as conn:
        row = await conn.session.get(RunRow, outcomes[winner_index])
        row.status = "success"
    assert await submit("three") != outcomes[winner_index]


@pytest.mark.asyncio
async def test_same_client_key_isolated_by_authenticated_identity(pg_runtime):
    from langgraph_runtime_pg.auth import Principal
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, ThreadRow
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    alice_thread, bob_thread = uuid4(), uuid4()
    async with connect() as conn:
        conn.session.add(AssistantRow(assistant_id=assistant_id, graph_id="test", name="test"))
        conn.session.add_all([
            ThreadRow(thread_id=alice_thread), ThreadRow(thread_id=bob_thread)
        ])

    async def submit(identity, thread_id):
        async with connect() as conn:
            run = await RunRepository().create(
                conn.session,
                assistant_id=assistant_id,
                thread_id=thread_id,
                kwargs={"input": {}},
                metadata={},
                idempotency_key=Principal(subject=identity).idempotency_key("retry"),
            )
            return run.run_id

    alice = await submit("alice", alice_thread)
    bob = await submit("bob", bob_thread)
    assert alice != bob
    assert await submit("alice", alice_thread) == alice
