## ADDED Requirements

### Requirement: Complete v2 stream-part capture
For the locked LangGraph dependency version, GraphHarbor SHALL execute a graph
once and durably capture `values`, `updates`, `messages`, `custom`,
`checkpoints`, `tasks`, and `debug` v2 stream parts with their `type`, `ns`,
`data`, and interrupt fields preserved.

#### Scenario: Deterministic all-mode graph
- **WHEN** a graph emits state, a custom writer payload, checkpoints and task lifecycle data
- **THEN** every documented v2 mode is recorded with the expected payload and root namespace

#### Scenario: Nested graph event
- **WHEN** a graph invokes a child graph with subgraphs enabled
- **THEN** the recorded stream part identifies the child through a non-empty namespace

### Requirement: Standard v3 remote projection
GraphHarbor SHALL normalize captured v2 stream parts into ordered typed event
envelopes usable by its remote Agent Server stream, preserving namespace,
timestamp, data and interrupts for all standard modes.

#### Scenario: Replay of full stream channel set
- **WHEN** a completed run is replayed through a v3 remote stream
- **THEN** values, updates, messages, custom, checkpoints, tasks and debug events retain ordered sequence and payload

### Requirement: In-process event-stream boundary declaration
The compatibility documentation SHALL distinguish GraphHarbor's durable remote
event projection from LangGraph's in-process `RunStream` convenience objects,
and SHALL state how arbitrary custom payloads are retained.

#### Scenario: Compatibility review
- **WHEN** a maintainer reviews the declared in-process streaming support
- **THEN** the document lists covered raw modes, covered remote event channels, and excludes unexposed Python-only projection objects
