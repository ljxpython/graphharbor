## Context

GraphHarbor has a process-wide `AsyncPostgresStore` initialized with the
runtime lifecycle, plus a durable `RuntimeEventRow` log and Redis fanout for
thread events. Its public HTTP surface currently excludes the official Store
resource and `/threads/{thread_id}/stream`; those exclusions are explicit in
the compatibility manifest. The fixed `langgraph dev` baseline is the source
of truth for all public wire behavior.

## Goals / Non-Goals

**Goals:**

- Expose Store and thread-stream through the same REST/SSE contract observable
  through the official Python and JavaScript SDKs.
- Preserve PostgreSQL as durable source of truth and Redis solely as live
  transport/fanout.
- Make the official differential scenario the release gate, including replay
  and SDK calls.

**Non-Goals:**

- Do not replace the existing Store, event log, run stream or queue.
- Do not implement MCP, A2A, custom semantic search improvements, or a new
  streaming protocol.
- Do not infer contract details from undocumented SDK internals; record them
  from the pinned official service.

## Decisions

### Use existing persistence and transport

HTTP Store handlers adapt the initialized `AsyncPostgresStore`; they do not
access test-only `PgStore` or introduce a second table/schema. Thread-stream
replay reads `RuntimeEventRow` before subscribing to the existing Redis
thread stream, deduplicating by durable sequence.

Alternative considered: a dedicated Store API table and a separate SSE
broker. Rejected because both duplicate durable state and make restart/replay
semantics diverge from existing runtime behavior.

### Record the official wire contract before implementation

The compatibility harness starts the fixed official `langgraph dev` service.
Focused probes capture method, request body, status, headers, JSON payloads
and SSE frames for Store and thread stream. GraphHarbor implementation and
tests use those recordings; only documented dynamic identifiers/timestamps
are normalized.

Alternative considered: implement from SDK source alone. Rejected because the
public server's errors and SSE framing are the compatibility target.

### Scope resources by authenticated principal

Store keys and namespace operations must preserve tenant/project isolation in
the same way existing thread/run handlers do. The exact official scoping
fields and unauthorized/not-found behavior are determined by the recorded
baseline and covered by contract tests.

### Remove exclusions only with proof

Each removed exclusion requires an OpenAPI method/path match, an official
differential scenario and Python/JavaScript SDK coverage. This prevents a
declared capability from silently becoming a fabricated-success endpoint.

## Risks / Trade-offs

- [Official behavior differs from local assumptions] → Capture the fixed
  official service first and make its output the test fixture.
- [Redis disconnect during a long stream] → Replay durable events from
  PostgreSQL using the official cursor/reconnect mechanism before resuming
  fanout.
- [Store search relies on upstream indexing semantics] → Use the existing
  upstream Store implementation and test its externally observable behavior;
  do not implement custom search.
- [Long-lived subscriber leak] → Always remove the Redis queue in `finally`
  and cover disconnect/reconnect in tests.

## Migration Plan

1. Add contract recordings and failing differential/SDK tests.
2. Implement Store routes and thread stream using existing infrastructure.
3. Validate against dual services and resilience tests.
4. Remove the two exclusions and update the compatibility matrix only when all
   gates pass. Rollback is a normal application rollback; no destructive data
   migration is required.

## Open Questions

- The exact Store and thread stream public wire shape will be resolved from
  the pinned official runtime before code changes begin.
