# agent-server-protocol Specification

## Purpose
TBD - created by archiving change implement-production-agent-server. Update Purpose after archive.
## Requirements
### Requirement: Standard application loading
The server SHALL load the existing `langgraph.json` format, graph registrations, dependencies, env, auth and custom HTTP app without application graph source changes.

#### Scenario: Existing runtime configuration loads
- **WHEN** the server starts with the current `runtime_service/langgraph.json`
- **THEN** all registered graphs, auth handler and custom routes are discoverable

### Requirement: Core resource API
The server SHALL expose official-compatible assistants, threads, state/history, runs, batch runs, cancellation, copy/prune and cron operations with matching request validation, response fields and error semantics.

#### Scenario: Official SDK resource operations
- **WHEN** official Python or JavaScript SDK methods are called for each Core resource
- **THEN** the call succeeds or returns the documented HTTP error without a GraphHarbor-specific wrapper

### Requirement: Capability honesty
The server SHALL report `store` and other Extended/Unavailable capabilities explicitly and SHALL NOT return fabricated success responses for them.

#### Scenario: Unavailable capability request
- **WHEN** a client calls an Unavailable endpoint
- **THEN** the server returns a documented 404/501-style error and capability metadata remains accurate

