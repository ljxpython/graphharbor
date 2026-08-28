## MODIFIED Requirements

### Requirement: v3 graph events
The server SHALL expose v3-compatible typed projections for messages, values,
updates, custom, checkpoints, tasks, debug, tools, lifecycle and subgraph
events. Standard events SHALL retain their sequence, namespace, timestamp,
data and interrupt payloads through durable replay.

#### Scenario: Subgraph lifecycle
- **WHEN** a P0 graph invokes a child graph
- **THEN** start, emitted events, completion/failure and parent-child namespace relationships are observable

#### Scenario: Full standard stream replay
- **WHEN** a completed graph emitted every documented v2 stream mode
- **THEN** replay exposes each corresponding typed channel in sequence with its namespace and payload intact
