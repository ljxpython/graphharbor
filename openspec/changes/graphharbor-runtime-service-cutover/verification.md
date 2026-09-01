# Verification

## Pre-apply decision

The change is allowed to continue as a governed cross-repository implementation. Production
cutover is not approved. The route must remain on the existing runtime path until the readiness
checker reports `ready_for_cutover` and the owner approval is recorded.

The owner approved implementation of the foundational GraphHarbor fixes on 2026-09-01 while
explicitly retaining GraphHarbor as a generic Agent Server replacement. Application-specific Runtime
Principal, Policy, graph and tool semantics remain outside this repository.

## Checks

| Check | Result | Evidence |
|---|---|---|
| Formal change validation | passed | `openspec validate graphharbor-runtime-service-cutover --type change --strict` |
| Locked revisions | passed | GraphHarbor `9ba6bf5839e713a943921bd24f625e1f4a350341`; runtime-service `f95967942cde346b7313d4a6eecee66396b5655f`; both repositories have reviewed local changes |
| Locked packages | passed | `graphharbor==0.13.0.post17`, `graphharbor-runtime==0.13.0.post17`, `langgraph==1.2.11`, `langgraph-sdk==0.4.3`; `uv.lock` and both built wheel/sdist sets |
| Supported runtime-service exports | passed | Production `reference_agent`; acceptance config additionally loads `workflow_demo`, `deep_agent_demo`, `mcp_demo` and `backend_demo` |
| GraphHarbor protocol/runtime suite | passed | `132 passed, 14 skipped`; database tests used the isolated non-superuser application role, PostgreSQL 16.9 and Redis 7.4.2 |
| Static and version gates | passed | Ruff, mypy, `uv lock --check` and `scripts/check_versions.py` |
| Runtime-service graph loading | passed | `GraphRegistry.from_path()` and per-Run `RunnableConfig`/`langgraph_auth_user` probes; final wheels loaded `langgraph.demo.json` |
| Runtime-service real API/Worker chain | passed for covered R6 cases | Final wheels: real `reference_agent` sync/checkpoint run plus deterministic two-interrupt workflow; durable suite `6 passed, 4 skipped` |
| Isolated migrations/readiness | passed | Repeated migration, no Alembic drift, advisory-lock serialization and application/Checkpointer/Store head readiness |
| Worker checkpoint takeover | partial | Real-process `SIGTERM` and `SIGKILL` both recovered the same Run to `success` with one terminal event; claim/finalize injection boundaries remain open |
| Terminal idempotency | passed | Duplicate queue hints produced one claim; reaper/cancel/late-finalize race produced one terminal event and no remaining lease |
| Worker run deadline | passed | Optional positive `GRAPHHARBOR_RUN_TIMEOUT_SECONDS`; PostgreSQL contract persisted `timeout/timeout`, removed the lease and wrote exactly one terminal event; full production contract `48 passed`, targeted Ruff and mypy passed |
| Python install matrix | passed | Built wheels installed and started on Python 3.11, 3.12 and 3.13 |
| Production cutover readiness | not_ready | `artifacts/cutover-gates.json` has all hard gates `not_run` and `owner_approved=false` |

## Uncovered boundaries

- Real platform-issued policy JWT across API, two Workers and two tenant/project scopes.
- Worker termination at claim and finalize boundaries; the current process harness covers checkpoint takeover.
- DeepAgent marker recovery and production Sandbox isolation across Worker replacement.
- Production MCP scope, collision and resource cleanup cases.
- Real Langfuse/OTLP query and exporter fault matrix.
- Cross-network SSE disconnect/reconnect through a second host or controlled proxy.
- Migration backup/restore, performance and rollout rollback.
- Platform-api gateway route ownership and the `0% -> 1% -> 10% -> 50% -> 100%` rollout flag.

## Documentation impact

- `.env.example` now uses the platform-api delegation audience `runtime-service`.
- `docs/production-runbook.md` distinguishes the external delegation JWT audience from the
  internal signed RuntimeContext audience `graphharbor-worker`.
- `scripts/check_cutover_readiness.py` is the only readiness decision helper; it fails closed and
  requires every passed gate to reference an existing repository evidence file.
