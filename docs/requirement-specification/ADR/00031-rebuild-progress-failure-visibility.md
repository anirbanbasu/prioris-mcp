---
status: accepted
date: 2026-08-16
---

# ADR-00031: Rebuild progress exposes succeeded/failed counts, not an in-process retry policy

## Context

A follow-up branch review found that `VectorRebuildProgress`'s two-field counter (`total`/`remaining`) only ever decremented `remaining` after a *successful* re-embed write. `EmbeddingScheduler` catches and only logs every non-cancellation failure, then drops the task with no callback to its owner ([`scheduler.py`](../../../src/prioris_mcp/vector/scheduler.py)). A transient failure (model/storage/read hiccup) therefore left `remaining > 0` forever, with no live task for the affected item and no way for a caller polling `research://vector-index/rebuild-status` to distinguish "still running" from "silently stuck" — the resource's shape gave no evidence either way.

## Decision

Replace the two-field counter with a per-mechanism `{total, pending, succeeded, failed, active}` shape (`VectorRebuildProgress`/`VectorRebuildMechanismStatus`). Each re-embed wrapper (`_schedule_document_reembed`/`_schedule_note_reembed` in `server.py`) wraps its work in `try`/`except`/`else`: a completed write calls `*_succeeded()`, an ordinary exception calls `*_failed()` (both always decrement `pending`), and `asyncio.CancelledError` calls `*_cancelled()` (decrements `pending` without counting as either) before re-raising, so shutdown and mid-flight deletion stay distinguishable from a real failure without the wrapper swallowing cancellation itself. `EmbeddingScheduler`'s own exception handling and logging are untouched — the wrapper's `except`/`else` runs *inside* the scheduled coroutine, then re-raises, so the scheduler's existing `logger.exception(...)` still fires exactly once per failure; this only adds progress-counter bookkeeping alongside it, not a second log site or a change to how the scheduler itself handles exceptions.

No in-process retry is added. A failed item stays failed until whatever recovers it externally: a subsequent restart re-derives the "not yet ready" set from persisted status (`_reconcile_documents`/`_reconcile_notes` already only schedule items that aren't ready — a failed one still qualifies) and re-schedules it from scratch, the same self-healing property the rest of this design already relies on.

## Alternatives considered

- **Bounded retry with backoff inside `EmbeddingScheduler`** — rejected for now: it would recover from transient failures without a restart, but requires designing a retry/backoff policy, deciding how retries interact with `cancel()`/dirty-reschedule (`EmbeddingScheduler` already reschedules a key if a newer factory arrives while one is in flight — retries would need to compose with that, not race it), and meaningfully more test surface for a scheduler that today is a straightforward fire-and-forget tracker. Nothing currently demonstrates transient (as opposed to persistent/configuration) failures are common enough to justify that cost; the restart-driven self-heal already recovers eventually.
- **Preserve/re-expose the specific failed keys for a deliberate targeted retry** — rejected for now as more than the resource needs to answer "is this stuck": a count is sufficient to distinguish "still building" from "N failures, nothing left running," and a key list adds response-shape surface (unbounded size, needs its own pagination policy) for a capability (targeted retry) this design doesn't otherwise offer.

## Consequences

`research://vector-index/rebuild-status` can now show `{"total": 10, "pending": 0, "succeeded": 6, "failed": 4, "active": false}` — clearly terminal-and-partially-failed — instead of `{"total": 10, "remaining": 4}`, which was indistinguishable from "4 items still genuinely in flight." `active` (`pending > 0`) is a convenience field a caller could otherwise derive itself; it's included so a client doesn't have to know that convention. A restart remains the only recovery path for a failed item — this is a deliberate, documented limitation, not an oversight, matching the [Referenced from] ADR's existing "self-healing on restart" design for `building`/reconciliation state generally.

## Referenced from

- [Vector search → Reconciliation runs automatically at server startup](../search/02-vector-search.md#reconciliation-runs-automatically-at-server-startup)
- [ADR-00030: Corpus-wide vector-index reconciliation runs automatically at server startup, not as a tool](00030-vector-index-reconciliation.md)
