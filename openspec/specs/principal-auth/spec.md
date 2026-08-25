# principal-auth Specification

## Purpose
TBD - created by archiving change implement-production-agent-server. Update Purpose after archive.
## Requirements
### Requirement: Delegation JWT authentication
Production integration SHALL accept short-lived platform-api delegation JWTs, validate signature, issuer, audience, expiry, `sub`, tenant/project, role/scopes and `jti`, and create one normalized Principal.

#### Scenario: Valid delegation
- **WHEN** a request carries a valid delegation JWT
- **THEN** Agent Server and custom routes observe the same Principal identity and tenant/project scope

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

