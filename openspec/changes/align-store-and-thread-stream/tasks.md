## 1. Official Contract Discovery

- [x] 1.1 Start the pinned official service and record Store methods, request/response validation and SDK calls.
- [x] 1.2 Record the official thread-stream request, SSE frames, terminal behavior and reconnect cursor semantics.
- [x] 1.3 Extend the differential scenario/runner to compare the recorded Store and thread-stream paths without exclusions.

## 2. Store API

- [x] 2.1 Add principal-scoped Store handlers by adapting the existing `AsyncPostgresStore`.
- [x] 2.2 Register only the official Store routes and match official validation/status/payload behavior.
- [x] 2.3 Add focused Store REST, persistence and official Python/JavaScript SDK contract tests.

## 3. Thread Stream API

- [x] 3.1 Add the official thread-stream handler using durable event replay and existing Redis thread fanout.
- [x] 3.2 Implement official disconnect, terminal and reconnect behavior without duplicating accepted events.
- [x] 3.3 Add focused SSE frame/order/reconnect tests and official SDK contract coverage.

## 4. Compatibility Gate

- [x] 4.1 Remove Store and thread-stream exclusions and expand the manual compatibility workflow scenario.
- [x] 4.2 Run focused tests, full relevant suites, baseline validation and strict OpenSpec validation.
- [x] 4.3 Run the real dual-service official protocol comparison and update compatibility documentation with the verified result.
