## Why

GraphHarbor currently aligns the Core REST surface and default run SSE output,
but its public API deliberately excludes the official Store client endpoints
and the official thread-scoped stream. Internal PostgreSQL Store and
Redis-backed event infrastructure already exist; exposing them through the
official contract makes the Python and JavaScript SDK surface complete for
these capabilities.

## What Changes

- Expose the official Store HTTP resource operations using the existing
  PostgreSQL-backed Store, including item reads/writes/deletes, search and
  namespace listing.
- Expose the official thread-scoped SSE endpoint using the existing durable
  runtime-event log and Redis fanout, with replay/reconnect semantics.
- Compare Store and thread-stream request/response/SSE contracts against the
  fixed `langgraph dev` baseline and exercise them through official SDKs.
- Remove Store and thread-stream entries from the protocol-exclusions manifest
  only after the differential and SDK tests pass.

## Capabilities

### New Capabilities

- `extended-store-api`: Official-compatible HTTP Store operations backed by
  GraphHarbor's PostgreSQL Store.

### Modified Capabilities

- `agent-server-protocol`: Store transitions from explicitly unavailable to an
  official-compatible public SDK resource.
- `event-streaming`: Add the official thread-scoped stream contract alongside
  the existing v2 protocol event stream.
- `runtime-persistence`: Persist and scope Store resource data through the
  existing PostgreSQL source of truth.

## Impact

- Affected code: `langhost` routes/handlers and `langgraph-runtime-pg` Store
  and event infrastructure adapters.
- Affected public API: `/store/*` and `/threads/{thread_id}/stream`.
- Affected validation: official differential scenario, Python/JavaScript SDK
  contracts, reconnect coverage and compatibility exclusions/matrix.
- No new infrastructure or production dependencies are introduced.
