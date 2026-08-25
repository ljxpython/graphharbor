## ADDED Requirements

### Requirement: Fixed official protocol baseline
GraphHarbor SHALL associate every released GraphHarbor version with one fixed `langgraph`, `langgraph-sdk`, `langgraph-cli` and official Agent Server compatibility version in the compatibility matrix.

#### Scenario: Upgrade mapping is recorded
- **WHEN** LangGraph-related dependencies are upgraded for a GraphHarbor release
- **THEN** the compatibility matrix records the GraphHarbor version, all fixed upstream versions and successful official protocol comparison status

### Requirement: Official output differential gate
The project SHALL provide a comparison command that accepts an official service URL and a GraphHarbor service URL, normalizes only documented dynamic values, and fails when their supported public HTTP, OpenAPI path/method or SSE output differs. HTTP status and media type remain strict; framework-generated OpenAPI descriptions and schemas are not compared as wire output.

#### Scenario: Supported response differs
- **WHEN** a supported endpoint returns a different status, required header, JSON value, OpenAPI path/method, SSE event order or SSE payload
- **THEN** the comparison command exits unsuccessfully and reports the normalized differing path and values

### Requirement: Explicit dependency-upgrade verification
The project SHALL run the official output differential gate only through an explicit compatibility-upgrade workflow or equivalent documented upgrade command; normal CI SHALL NOT poll or automatically update upstream versions.

#### Scenario: Dependency upgrade is proposed
- **WHEN** a maintainer updates a LangGraph-related dependency
- **THEN** the maintainer starts the compatibility-upgrade workflow against the intended official version before updating the release mapping

### Requirement: Documented exclusions
The comparison command SHALL ignore only UUIDs, timestamps, generated service locations and other explicitly documented dynamic values; unsupported official capabilities SHALL be recorded as exclusions rather than silently skipped.

#### Scenario: New official endpoint appears
- **WHEN** the official OpenAPI output exposes an endpoint absent from GraphHarbor
- **THEN** the comparison fails unless the endpoint is listed in the documented capability exclusions
