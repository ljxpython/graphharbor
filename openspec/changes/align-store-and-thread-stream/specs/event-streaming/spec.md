## ADDED Requirements

### Requirement: Official thread-scoped stream
The server SHALL expose `/threads/{thread_id}/stream` with the exact pinned
official `langgraph dev` HTTP and SSE contract, including its request shape,
event framing, event order, lifecycle output and terminal behavior.

#### Scenario: Thread stream observes a run
- **WHEN** a client subscribes to a thread stream and a run is started for the
  thread
- **THEN** the client receives the same ordered SSE event sequence as the
  fixed official service

### Requirement: Thread-stream reconnect
The server SHALL honor the pinned official reconnect cursor semantics and
replay only subsequent durable thread events before resuming live delivery.

#### Scenario: Reconnect after an accepted event
- **WHEN** a client reconnects using the last accepted official cursor after a
  thread event has been emitted
- **THEN** it receives the same subsequent frames and no duplicate accepted
  frame as the fixed official service
