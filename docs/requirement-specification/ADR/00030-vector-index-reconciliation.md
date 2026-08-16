---
status: accepted
date: 2026-08-16
---

# ADR-00030: Corpus-wide vector-index reconciliation runs automatically at server startup, not as a tool

## Context

A follow-up branch review found that changing `PRIORIS_MCP_EMBEDDING_MODEL` left the vector corpus in a state the SRS didn't actually implement. [Index status is per-document/note](../search/02-vector-search.md#index-status-is-per-documentnote-derived-by-comparing-recorded-vs-configured-model) already documented that a model change makes every existing record `stale` and that "corpus-wide reindexing isn't a structurally different operation from per-document embedding, just the same per-document trigger invoked in bulk" — but no bulk-reindex trigger, startup reconciliation, or user-facing reindex operation existed to actually invoke it. The only scheduling sites were fresh parses and note create/text-update. Worse, for a dimension-changing model switch, both vector backends' `_connect()` drops the entire `vec0` table the moment any ordinary read (`search`, `count`, `status`) reaches it — so the first client request after a dimension change, not a deliberate rebuild step, was what silently emptied the corpus, with nothing left to repopulate it. A same-dimension model rename was worse in a different way: old rows stayed searchable and matched against a newer query embedding despite living in a different model's embedding space, and an unscoped document search (missing `provider`/`identifier`/`format`) had no `index_status` at all to warn a caller this was happening.

## Decision

Reconciliation is triggered automatically at server startup, via FastMCP's `lifespan`, rather than exposed as a new MCP tool a caller has to remember to invoke. It has two phases with deliberately different blocking behaviour:

- **`_force_vector_reconnect()`** forces both vector backends' `_connect()` to run immediately, so any destructive model-mismatch table drop happens once, at a known point, before any client request can trigger it as a side effect. This step blocks the server's readiness — measured at ~2.9–21.8ms even at 50,000 indexed chunks, since it touches only backend connection state, not the corpus.
- **`reconcile_vector_index()`** enumerates every persisted document/note not yet ready under the configured model and schedules each for re-embedding via the same per-item `EmbeddingScheduler` trigger every other indexing path already uses. This step runs as a background `asyncio` task, deliberately not blocking server readiness — corpus size is unbounded and there's no reason a client's handshake should wait on it, unlike the bounded, backend-connection-scoped work above.

Re-embedding itself is bounded by `EmbeddingScheduler(max_concurrent=PRIORIS_MCP_EMBEDDING_MAX_CONCURRENCY)`, a plain `asyncio.Semaphore` acquired for a task's true lifetime — not a batching scheme or a separate job queue — so a large reconciliation run can't storm the process with unbounded concurrent embedding tasks, sharing the same bound a live per-document/per-note trigger already uses.

Reconciliation's live progress (`VectorRebuildProgress`, exposed via the `research://vector-index/rebuild-status` resource) is process-local, in-memory state, not persisted — the same "derived transiently, self-healing on restart" property `building` status already has. A restart doesn't need to resume a prior run's counters; it re-derives the same not-yet-ready set from persisted vector status and re-schedules it from scratch.

## Alternatives considered

- **A new MCP tool the caller invokes to trigger reconciliation** — rejected: it reintroduces the same "caller has to remember to ask" gap this fixes in the first place, and there's no reason reconciliation should ever be optional after a model change — it should just happen.
- **Blocking server startup on the full corpus reconciliation** — rejected: corpus size is unbounded, so this would make server startup latency a function of corpus size instead of a near-constant few milliseconds, for a background maintenance operation that doesn't need to complete before the server can usefully serve requests. The server is already usable the moment it starts accepting requests — FTS search and `index_status` (including this reconciliation run's own live progress) are both accurate and available throughout, even though the affected corpus's vector *content* is not (see Consequences below): blocking startup on the full rebuild wouldn't change when vector search itself becomes usable, only when everything else does too.
- **Batching or a persistent job queue for the re-embed backlog** — rejected as unnecessary machinery: `EmbeddingScheduler`'s existing semaphore-bounded fire-and-forget task tracking already does the job, and a restart's self-healing re-derivation (see [Decision](#decision) above) removes any need for the backlog itself to survive a restart.

## Consequences

A same-dimension model rename is now also correctly detected and reconciled, not just a dimension-changing one — closing the review's Low finding about same-dimension changes going undetected, alongside the review's High finding about the missing recovery path itself. A follow-up review round found that the initial fix still let a model-mismatch drop leave the previous model's status rows in place, letting a rollback (`model-a` → `model-b` → `model-a`) resurrect a dropped generation's row as falsely `ready`; see [Vector search → A model-mismatch drop invalidates status rows too, not just the vector table](../search/02-vector-search.md#a-model-mismatch-drop-invalidates-status-rows-too-not-just-the-vector-table) for that fix and its accepted trade-off (`stale` is no longer observable in practice; a caller sees `not_built` instead). `index_status` itself is never blocked or frozen during a reconciliation run — a caller polling `index_status` (or the new resource) sees `not_built`/`building` progress toward `ready` rather than a stuck value. This is not the same as vector search results themselves staying available: `_force_vector_reconnect()` deliberately drops and recreates the affected `vec0` table *before* reconciliation's re-embedding begins, so a model change (dimension-changing or same-dimension rename alike) empties that corpus's vector search results until each item is re-embedded — `index_status` accurately reports `not_built`/`building` for exactly that window rather than papering over it. FTS results are unaffected either way, since they don't share `vec0`'s table. Because reconciliation's progress counters are process-local, restarting mid-run loses visibility into "how far did that run get" — the next run recomputes its own total from scratch rather than resuming a persisted count; this is consistent with `building` status's existing non-persistence, not a new inconsistency.

## Referenced from

- [Vector search → Reconciliation runs automatically at server startup](../search/02-vector-search.md#reconciliation-runs-automatically-at-server-startup)
