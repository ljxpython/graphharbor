## MODIFIED Requirements

### Requirement: Local process deployment
The project SHALL provide separate API, worker and migration commands that run against host PostgreSQL and Redis without Docker, and SHALL provide production health, version and route configuration for the runtime-service cutover.

#### Scenario: Local acceptance startup
- **WHEN** an operator starts migration, API and at least two Workers with the documented commands
- **THEN** readiness succeeds and an official SDK can execute a runtime-service graph using the isolated database and Redis namespace

### Requirement: Composed lifespan
The runtime SHALL initialize server, GraphHarbor runtime and custom application resources in order, and SHALL drain/requeue work before closing resources on shutdown.

#### Scenario: Graceful shutdown
- **WHEN** the API or worker receives SIGTERM
- **THEN** readiness becomes false, new work is stopped, active work is drained or requeued, and resources close in reverse dependency order

### Requirement: Operational health
The deployment SHALL expose readiness/liveness, structured logs and Prometheus/OTel-compatible run, queue, worker, stream, PostgreSQL and Redis signals without requiring a hosted observability service.

#### Scenario: Dependency unavailable
- **WHEN** PostgreSQL or Redis is unavailable during startup
- **THEN** readiness remains false and the process reports an actionable health reason
