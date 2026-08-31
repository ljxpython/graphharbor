# Verification

## Pre-apply decision

The change is allowed to continue as a governed cross-repository implementation. Production
cutover is not approved. The route must remain on the existing runtime path until the readiness
checker reports `ready_for_cutover` and the owner approval is recorded.

## Checks

| Check | Result | Evidence |
|---|---|---|
| Formal change validation | passed | `openspec validate graphharbor-runtime-service-cutover --type change --strict` |
| GraphHarbor protocol/runtime unit coverage | passed for non-database tests | Existing runtime, auth, workspace, observability and protocol test artifacts |
| PostgreSQL contract suite with the default environment | not usable | The default DSN points to `localhost:5432/langgraph`, where the `postgres` role does not exist; this is not acceptance evidence |
| Runtime-service graph loading | passed in the runtime-service Python environment | Existing loader probe and `GraphRegistry.open("reference_agent", ...)` probe |
| Production cutover readiness | not_ready | `artifacts/cutover-gates.json` has all hard gates `not_run` and `owner_approved=false` |

## Uncovered boundaries

- Real platform-issued policy JWT across API, two Workers and two tenant/project scopes.
- DeepAgent marker recovery and production Sandbox isolation across Worker replacement.
- Production MCP scope, collision and resource cleanup cases.
- Real Langfuse/OTLP query and exporter fault matrix.
- Cross-network SSE disconnect/reconnect through a second host or controlled proxy.
- Python 3.11/3.12/3.13 isolated install, migration backup/restore, performance and rollout rollback.
- Platform-api gateway route ownership and the `0% -> 1% -> 10% -> 50% -> 100%` rollout flag.

## Documentation impact

- `.env.example` now uses the platform-api delegation audience `runtime-service`.
- `docs/production-runbook.md` distinguishes the external delegation JWT audience from the
  internal signed RuntimeContext audience `graphharbor-worker`.
- `scripts/check_cutover_readiness.py` is the only readiness decision helper; it fails closed and
  requires every passed gate to reference an existing repository evidence file.
