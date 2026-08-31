## ADDED Requirements

### Requirement: Formal replacement readiness
GraphHarbor SHALL be eligible to replace the runtime-service execution plane only when every hard gate has reproducible `passed` evidence from the locked runtime-service and GraphHarbor versions.

#### Scenario: Missing hard gate blocks cutover
- **WHEN** any factory, durability, authorization, DeepAgent, observability, network, migration or rollback gate is `blocked`, `failed` or `not_run`
- **THEN** the route remains on the existing runtime path and the readiness result is `not_ready`

### Requirement: Controlled route cutover
The platform gateway SHALL route new runtime-service Runs through GraphHarbor by an explicit Agent/tenant/project or percentage flag and SHALL retain an immediate route rollback.

#### Scenario: Rollback stops new assignments
- **WHEN** the GraphHarbor route flag is disabled during a rollout
- **THEN** new Runs use the prior runtime path, existing Run route ownership is unchanged, and no Run or durable event is deleted

### Requirement: Cross-repository version evidence
The cutover SHALL record the runtime-service source revision, GraphHarbor revision, dependency lockfiles, migration head, Redis namespace and acceptance artifact identifiers.

#### Scenario: Evidence is reproducible
- **WHEN** an acceptance run is reviewed
- **THEN** an operator can recreate the same environment without reading secrets from the artifact
