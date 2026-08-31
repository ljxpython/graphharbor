## Context

`runtime-service` owns business graph composition and model/tool configuration. GraphHarbor owns the Agent Server execution plane: HTTP protocol, PostgreSQL durability, Redis coordination, Worker lifecycle, event replay and production authentication. The current GraphHarbor implementation has these pieces independently, but there is no governed contract proving that the external runtime-service project can be loaded, executed, recovered and rolled back as one production path.

The change crosses two repositories and several trust boundaries. It therefore requires an isolated PostgreSQL database, namespaced Redis, real Worker processes, a platform-issued delegation token, the locked runtime-service dependency set, a controlled network path and an observable Langfuse/OTLP endpoint.

## Goals / Non-Goals

**Goals:**

- Load the existing runtime-service `langgraph.json` and invoke each supported async per-Run factory through GraphHarbor.
- Preserve `platform-api` as the control plane and bind every Run to verified principal, tenant, project, thread, model and tool policy data.
- Prove durable recovery, terminal idempotency, event replay, HITL, DeepAgent isolation and exporter failure isolation with reproducible evidence.
- Provide a forward-compatible migration, an explicit feature-flagged cutover, immediate rollback and a readiness decision that cannot be green while a hard gate is missing.

**Non-Goals:**

- Moving business graph code, model providers, MCP credentials or Platform domain data into GraphHarbor.
- Replacing Langfuse Trace search, Platform authorization or the runtime-service Agent composition root.
- Supporting arbitrary host filesystem access, implicit tool discovery or unbounded subagents in production.
- Migrating old checkpoints without a compatibility test and an explicit owner decision.

## Decisions

### 1. GraphHarbor is the execution plane

`platform-api` selects the runtime route and signs the delegation context. GraphHarbor validates it, loads the graph config and executes the graph. The browser and ordinary clients never receive a GraphHarbor worker credential or call a graph directly.

The existing `GraphRegistry.open()` path is the only factory boundary. It supports a static graph, a zero-argument factory and a one-argument `RunnableConfig` factory, and closes async context-manager resources after each Run. No second public Builder or Registry is introduced.

### 2. PostgreSQL is authoritative

PostgreSQL stores Run, Thread, Checkpoint, Lease, retry and terminal event facts. Redis is limited to queue hints, Pub/Sub, cancellation and bounded replay. Worker recovery scans PostgreSQL when Redis hints are missing, and terminal writes use lease ownership plus a uniqueness/conditional-write guard.

### 3. Trust is signed and immutable per Run

Production API requests require the platform delegation JWT. The API creates a signed RuntimeContext envelope containing `run_id`, `thread_id`, tenant/project, principal identity and a policy snapshot. The Worker verifies the envelope again and injects only verified values into `configurable`; client input cannot override them.

### 4. Agent capabilities are explicit intersections

Each runtime-service Agent declares its maximum model/tool/Backend/Skill/Subagent set. The effective set is the intersection of that declaration, the signed RuntimePolicy and the Subagent-specific restrictions. DeepAgent workspace resources are Thread-scoped and either use persisted StateBackend state or an isolated Sandbox; direct host filesystem access is an acceptance-only fixture.

### 5. Observability is fail-soft but queryable

Only an allowlist of correlation fields enters Langfuse metadata. Prompt, response and tool arguments are masked or summarized. Export is bounded and asynchronous; exporter errors increment metrics and logs but never change Run finalization. Acceptance queries the external endpoint rather than trusting local callback construction.

### 6. Cutover is a route decision, not a data rewrite

Deploy GraphHarbor alongside the current runtime path. Start at 0%, then move through 1%, 10%, 50% and 100% by Agent/tenant/project or percentage. New Runs use one selected execution path; existing Runs finish on their recorded route. Rollback disables new GraphHarbor routing and leaves the shared schema forward-compatible.

The `platform-api` `runtime_gateway` owns this decision. It selects the route once when a
Run is created, persists `runtime_route` (`legacy` or `graphharbor`) together with the
platform durable Run record, and uses that value for every later state, stream, command,
join, cancel and delete request. A percentage increase must not move an existing Run to a
different upstream. Disabling the GraphHarbor flag changes only new assignments; it must
not delete or rewrite existing Run, Event or Checkpoint facts.

## Risks / Trade-offs

- [Factory or dependency drift] -> Pin runtime-service and GraphHarbor lockfiles; run startup signature checks and a compatibility suite for every upgrade.
- [A stale Worker overwrites a terminal Run] -> Require lease ownership and conditional terminal writes, then inject a late finalize in the isolated database.
- [StateBackend is mistaken for a production filesystem] -> Separate state-backed and Sandbox-backed acceptance graphs; forbid host filesystem mode in the production profile.
- [Trace export leaks content or blocks execution] -> Apply metadata allowlist/masking before callback binding and inject 401/429/5xx/timeout/queue-full faults.
- [Schema rollback loses new facts] -> Prefer code rollback against a forward-compatible schema; downgrade only after an isolated migration rollback rehearsal.
- [Network replay hides event loss] -> Use an external client or controlled proxy, record event IDs/sequences and compare the final PostgreSQL terminal event with the client stream.

## Migration Plan

1. Build and pin GraphHarbor plus the runtime-service environment; run migration in a new database and verify the recorded head.
2. Start API and at least two Workers with a unique Redis prefix. Run deterministic protocol and fault tests before enabling external model traffic.
3. Enable the platform route at 1% for a test tenant/project. Verify Run/event/checkpoint joins, metrics, traces and rollback.
4. Expand through 10%, 50% and 100% only when every hard gate is `pass`. Keep the old runtime route available for immediate new-Run fallback.
5. Roll back by stopping new GraphHarbor assignments, preserving the schema, allowing in-flight GraphHarbor Runs to drain or be explicitly recovered, and routing new Runs to the prior path. Do not delete shared data during rollback.

## Open Questions

- Which production Sandbox provider supplies the runtime-service workspace, and what persistent binding identifies it across Workers?
- Which migration adds `runtime_route` to the platform-api durable Run record, and how is the
  legacy-to-GraphHarbor Run ID mapping exposed for operations and support?
- What real Langfuse/OTLP endpoint and retention policy are approved for acceptance?
- Which external model and MCP credentials are available for the gated acceptance window?
- Is there an approved owner and maintenance window for isolated migration, data cleanup and kill/restart fault injection?
