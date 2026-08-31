## MODIFIED Requirements

### Requirement: Remote v2 run stream
The server SHALL support official `client.runs.stream(..., version="v2")` calls with stream modes, subgraph output, heartbeat, terminal events and resumable replay across Worker and API replacement.

#### Scenario: v2 reconnect
- **WHEN** a client reconnects with its last accepted cursor after a network or Worker interruption
- **THEN** the server replays subsequent events in order or returns an explicit cursor-too-old result

### Requirement: Protocol v2 thread event stream
The server SHALL support thread-scoped event subscriptions and commands using the official event envelope, channel filters, namespace filters and `since` replay.

#### Scenario: Thread event subscription
- **WHEN** a client subscribes to lifecycle and messages channels for a thread
- **THEN** matching root and requested subgraph events arrive with sequence, namespace and run identifiers

### Requirement: v3 graph events
The server SHALL expose v3-compatible typed projections for messages, values, updates, custom, tools, lifecycle and subgraph events.

#### Scenario: Subgraph lifecycle
- **WHEN** a runtime-service P0 graph invokes a child graph
- **THEN** start, emitted events, completion/failure and parent-child namespace relationships are observable
