---
status: accepted
date: 2026-08-12
---

# ADR-00025: `index_status` travels with every search response, not a separate status tool

## Context

`fts`/`vector` mechanisms become ready at different times (FTS is synchronous and near-immediate; vector needs an embedding pass), so a caller needs some way to know a mechanism isn't ready yet rather than mistaking a `not_built`/`stale` mechanism's empty results for "no matches."

## Decision

`index_status` is folded into the same response `search`/`research_notes_search` already returns (e.g. `{"fts": "ready", "vector": "stale"}`), not gated behind a separate status-check call.

This does not mean *unconditionally present with every mechanism's status on every call* — see [Vector search → `index_status` travels with every search response, not a separate status-check call](../search/02-vector-search.md#index_status-travels-with-every-search-response-not-a-separate-status-check-call) for the actual per-corpus population rules (documents: gated on `provider`/`identifier`/`format` all being given; notes: gated on the vector mechanism having actually run, and never carrying an `"fts"` key at all). What this ADR settles is narrower and still holds: *when* `index_status` is populated, it rides along with the ordinary search response rather than requiring a dedicated call.

## Alternatives considered

- **A separate status-check tool, called before or instead of `search`** — rejected: it reintroduces exactly the ambiguity this is meant to prevent, since a caller who doesn't think to call it just sees empty results with no explanation.

## Consequences

Folding status into the response means the explanation travels with the result whenever it's populated, and lets a caller retry the same `search` call as a natural readiness-polling loop instead of orchestrating a separate check-then-search sequence. `fts` is included in the documents side of `index_status` despite being synchronous and normally immediate, because "normally" isn't "always" — a missing FTS entry for a document that does exist (a failed/partial `index_entries` call, external corruption, a manually deleted `search.sqlite3`) is a real integrity gap this field surfaces instead of silently assuming away.

## Referenced from

- [Vector search → `index_status` travels with every search response, not a separate status-check call](../search/02-vector-search.md#index_status-travels-with-every-search-response-not-a-separate-status-check-call)
