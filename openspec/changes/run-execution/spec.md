## MODIFIED Requirements

### Requirement: Durable run state machine
The system SHALL persist run status as `pending`, `running`, `success`, `error`, `timeout` or `interrupted`, and SHALL persist an internal reason for HITL, cancellation, shutdown, timeout and retry outcomes.

#### Scenario: Cancelled run status
- **WHEN** an active run is cancelled and cancellation completes
- **THEN** its public status is `interrupted` and its internal reason is `cancel_requested`

### Requirement: HITL resume
The system SHALL persist interrupt payloads and resume the same thread/checkpoint through `Command(resume=...)`.

#### Scenario: Duplicate resume
- **WHEN** the same resume command is submitted twice
- **THEN** the second request is idempotent and does not repeat completed tool side effects

### Requirement: Worker recovery and retry
The system SHALL use a PostgreSQL lease and heartbeat to ensure one active worker claim per run, reclaim expired claims, reject stale terminal writes and retry infrastructure failures no more than three times.

#### Scenario: Worker failure
- **WHEN** a worker dies after claiming a run
- **THEN** the reaper reclaims the lease and the run is retried or terminally errored according to the retry limit, while a late completion cannot change a committed terminal state

### Requirement: Official cancellation semantics
The system SHALL support single and bulk cancellation with `wait`, `action=interrupt`, `action=rollback`, thread/run/status filters and idempotent behavior.

#### Scenario: Rollback cancellation
- **WHEN** a client cancels a run with `action=rollback`
- **THEN** execution stops and the run plus associated checkpoint data are removed according to the documented contract
