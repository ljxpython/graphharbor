## ADDED Requirements

### Requirement: PostgreSQL source of truth
The system SHALL store durable assistants, threads, runs, status reasons, checkpoints, leases, retry metadata and migration version in PostgreSQL.

#### Scenario: Restart recovery
- **WHEN** the API and workers restart while a run is pending or interrupted
- **THEN** the run and checkpoint can be read and resumed from PostgreSQL

### Requirement: Redis transport isolation
The system SHALL use Redis for queue, Pub/Sub, cancellation and bounded replay with an instance-specific key prefix and SHALL tolerate Redis restart without losing durable run state.

#### Scenario: Redis restart
- **WHEN** Redis restarts during an active run
- **THEN** durable status remains in PostgreSQL and the system either resumes transport or reports a recoverable stream/queue error

### Requirement: Explicit migration
The system SHALL provide an idempotent migration command/job separate from normal server startup.

#### Scenario: Repeated migration
- **WHEN** migration is run against an empty database and then run again
- **THEN** both executions complete without destructive or duplicate schema errors
