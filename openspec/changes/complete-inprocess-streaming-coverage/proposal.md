## Why

GraphHarbor currently persists and projects the core v3 event path, but its
acceptance suite does not prove every LangGraph in-process v2 stream mode or
every standard v3 projection described by the supported LangGraph release.
This prevents an accurate complete compatibility declaration.

## What Changes

- Add a deterministic in-process streaming fixture covering all documented v2
  stream modes: values, updates, messages, custom, checkpoints, tasks, and
  debug, including subgraph namespaces and interrupt output.
- Preserve complete v3 protocol event fields when GraphHarbor durably records
  and replays graph execution events.
- Add direct LangGraph and GraphHarbor runtime contract tests for the standard
  v3 projections: event sequence, values/output, messages, subgraphs,
  interrupts, and arbitrary extension channels.
- Update capability documentation and acceptance mappings to distinguish the
  native in-process API from its remote Agent Server projection.

## Capabilities

### New Capabilities
- `inprocess-streaming-coverage`: Complete verified coverage of LangGraph v2
  stream parts and v3 event-stream projections for the locked dependency set.

### Modified Capabilities
- `event-streaming`: Durable GraphHarbor projection must retain the documented
  protocol envelope fields required for replay and remote consumption.

## Impact

Affected areas are the graph executor, worker event persistence, acceptance
fixtures and results, event-streaming specifications, and compatibility
documentation. No public product graph or provider dependency is added.
