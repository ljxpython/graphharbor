## MODIFIED Requirements

### Requirement: Delegation JWT authentication
Production integration SHALL accept short-lived platform-api delegation JWTs, validate signature, issuer, audience, expiry, `sub`, tenant/project, role/scopes, `jti` and model/tool policy claims, and create one normalized Principal and immutable policy snapshot.

#### Scenario: Valid delegation
- **WHEN** a request carries a valid delegation JWT with policy claims
- **THEN** Agent Server, Worker and custom routes observe the same Principal identity, tenant/project scope and policy snapshot

### Requirement: Authorization isolation
The system SHALL derive tenant and project filters from the Principal, SHALL reject client overrides, and SHALL return 404 for cross-tenant resource access.

#### Scenario: Cross-tenant lookup
- **WHEN** a principal requests a thread belonging to another tenant
- **THEN** the server returns 404 without revealing resource existence

### Requirement: Credential separation
Management credentials SHALL not access user thread/run data, and demo tokens/API keys SHALL be disabled by default in production.

#### Scenario: Management credential data access
- **WHEN** a management credential calls a user thread endpoint
- **THEN** the request is denied according to the documented authorization error

### Requirement: Worker context verification
The Worker SHALL verify the signed RuntimeContext envelope against the persisted Run and SHALL fail before model or tool execution when the envelope is missing, expired, altered or scoped to another Run.

#### Scenario: Altered context is rejected
- **WHEN** a Worker receives a Run whose signed context does not match its run/thread/tenant/project or policy snapshot
- **THEN** the Run reaches a stable authorization error and no model or tool side effect occurs

### Requirement: Custom-auth user propagation
The system SHALL preserve a JSON-compatible user mapping returned by a standard LangGraph custom-auth handler inside the signed RuntimeContext envelope and SHALL restore that mapping as `configurable.langgraph_auth_user` before a per-Run graph factory is called. GraphHarbor SHALL NOT depend on application-specific nested Principal or Policy field names.

#### Scenario: Per-Run factory receives authenticated user
- **WHEN** custom auth returns a valid generic user mapping and a Worker opens a dynamic graph factory
- **THEN** the factory receives the same signed user facts through `configurable.langgraph_auth_user` without receiving the bearer token
