## ADDED Requirements

### Requirement: Official Store resource API
The server SHALL expose the Store item, search and namespace operations
required by the pinned official `langgraph dev` OpenAPI contract, with matching
methods, validation, statuses, headers and JSON payloads.

#### Scenario: SDK Store lifecycle
- **WHEN** an official Python or JavaScript SDK client writes an item, reads
  it, searches it, lists its namespace and deletes it
- **THEN** each operation has the same observable result as the fixed official
  service and the deleted item is no longer returned

### Requirement: Store resource isolation
The server SHALL scope Store operations to the authenticated principal using
the same tenant/project isolation boundary as other durable resources.

#### Scenario: Cross-principal Store read
- **WHEN** a second principal requests an item written by another principal
- **THEN** the server returns the same inaccessible-resource outcome as the
  fixed official service and exposes no item data
