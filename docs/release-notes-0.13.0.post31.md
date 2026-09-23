# 0.13.0.post31: Worker concurrency and event batching

Status: CI failed before publication. The JavaScript SDK fixture used `typing.TypedDict` on Python 3.11, which Pydantic 2.13 rejected during schema generation. This version was not uploaded to PyPI; the corrected release is 0.13.0.post32.

This release carries forward the published 0.13.0.post30 source and fixes two production worker issues:

- `graphharbor worker --n-jobs-per-worker N` now runs up to N independent execution slots. `N_JOBS_PER_WORKER` is used when the option is omitted; one slot remains the default. One lease reaper serves the process.
- v3 `messages/content-block-delta` events are durably written in bounded batches and fanned out through Redis pipelines. Non-delta and terminal events flush preceding deltas first, preserving event IDs, sequence, replay, and cancellation behavior.

Local PostgreSQL/Redis checks passed with four concurrent threads and 11,208 ordered deltas. In a single 1,000-event local comparison, PG+Redis time fell from 7.06 s to 0.65 s. Two concurrent real-model sessions and a 459-message long-output session completed. These measurements do not establish production p95; observe it after deployment.

No business-specific model, provider, tool, or trace field was added. Existing business-boundary cleanup remains tracked in `docs/projects/20260923-worker-concurrency-event-flush/open-issues.md`.
