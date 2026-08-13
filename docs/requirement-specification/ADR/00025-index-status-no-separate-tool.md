---
status: accepted
date: 2026-08-12
---

# ADR-00025: `index_status` travels with every search response, not a separate status tool

## Context

`fts`/`vector`/`graph` mechanisms become ready at different times (FTS is synchronous and near-immediate; vector needs an embedding pass; graph indexing will likely be slower still), so a caller needs some way to know a mechanism isn't ready yet rather than mistaking a `not_built`/`stale` mechanism's empty results for "no matches."

## Decision

Every search response carries an `index_status` field per mechanism (e.g. `{"fts": "ready", "vector": "stale", "graph": "not_built"}`) unconditionally — not only when results are empty, and not gated behind a separate status-check call.

## Alternatives considered

- **A separate status-check tool, called before or instead of `search`** — rejected: it reintroduces exactly the ambiguity this is meant to prevent, since a caller who doesn't think to call it just sees empty results with no explanation.

## Consequences

Folding status into every response means the explanation travels with the result whether or not the caller was looking for it, and lets a caller retry the same `search` call as a natural readiness-polling loop instead of orchestrating a separate check-then-search sequence. `fts` is included in `index_status` despite being synchronous and normally immediate, because "normally" isn't "always" — a missing FTS entry for a document that does exist (a failed/partial `index_entries` call, external corruption, a manually deleted `search.sqlite3`) is a real integrity gap this field surfaces instead of silently assuming away.

## Referenced from

- [Vector search → Search responses always carry per-mechanism `index_status`, including `fts`](../search/02-vector-search.md#search-responses-always-carry-per-mechanism-index_status-including-fts)
