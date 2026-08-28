## Context

The worker currently consumes `astream_events(version="v3")`. In the locked
LangGraph version this API owns its transformer mux and does not accept
`stream_mode`; a default stream therefore cannot prove every documented raw
v2 mode. The worker must execute a graph once, retain durable events, and keep
the existing Agent Server replay contract.

## Goals / Non-Goals

**Goals:**
- Capture every standard v2 `StreamPart` from a single graph execution.
- Normalize those parts into the existing durable v3-shaped event envelope.
- Verify namespace, interrupt, messages metadata, checkpoint/task/debug/custom
  payloads and arbitrary custom payloads through direct and runtime tests.
- Make the compatibility statement precise for the locked LangGraph release.

**Non-Goals:**
- Reimplement LangGraph's Python `RunStream` projection objects over HTTP.
- Invent a proprietary replacement for `stream.extensions`; arbitrary custom
  channel payloads are retained transparently, while transformer-owned Python
  projections remain an in-process LangGraph API.
- Change product graphs or add a model provider dependency.

## Decisions

1. The worker SHALL use `graph.astream(..., version="v2")` with all documented
   v2 modes and `subgraphs=True` as its durable event source. This executes the
   graph once and exposes the exact `StreamPart` shape LangGraph documents.
2. A small normalizer SHALL convert each `StreamPart` to the existing
   `{method, params:{namespace, timestamp, data, interrupts}}` envelope. This
   keeps persistence, replay, protocol SSE and run SSE on one representation.
3. The final `values` part supplies output; its `interrupts` field supplies
   `GraphOutput.interrupts`. This preserves the worker's run state machine
   without a second graph invocation.
4. Acceptance fixtures SHALL use deterministic nodes, a checkpointer and
   `get_stream_writer()` to create all modes without a remote model. Real
   model fixtures remain separate evidence for token providers.

Alternatives rejected: a second `astream_events` pass would execute tools and
side effects twice; accepting a caller-selected subset would make persisted
replay incomplete; fabricating extension projections would misrepresent the
LangGraph API.

## Risks / Trade-offs

- [Larger durable event volume from debug/checkpoint modes] → the full mode set
  is enabled only in the compatibility fixture; normal runs use the requested
  stream modes plus the required state modes.
- [LangGraph stream type changes on upgrade] → direct shape tests and the
  dependency-upgrade acceptance gate fail before a compatibility declaration
  is updated.
- [Remote JSON cannot preserve Python message classes] → serialize with the
  existing JSON conversion and assert protocol content/metadata, not identity.
