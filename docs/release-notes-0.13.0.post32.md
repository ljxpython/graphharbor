# 0.13.0.post32: Worker concurrency and event batching

Carries forward the published 0.13.0.post30 source. `graphharbor worker --n-jobs-per-worker N` now starts N independent execution slots; `N_JOBS_PER_WORKER` applies when the option is absent. v3 message deltas are persisted in bounded batches and then fanned out through Redis pipelines, preserving event order, cursor, replay, and terminal barriers.

Local PostgreSQL/Redis checks passed with four concurrent threads and 11,208 ordered deltas. A single 1,000-event PG+Redis comparison fell from 7.06 s to 0.65 s. Two concurrent real-model sessions and a 459-message long-output session completed. Production p95 and resource use still need observation after deployment.

The JavaScript SDK acceptance graph now imports `typing_extensions.TypedDict`, allowing schema generation on Python 3.11 with Pydantic 2.13. No business-specific provider, model, tool, or trace field was added. Existing business-boundary cleanup remains tracked in `docs/projects/20260923-worker-concurrency-event-flush/open-issues.md`.
