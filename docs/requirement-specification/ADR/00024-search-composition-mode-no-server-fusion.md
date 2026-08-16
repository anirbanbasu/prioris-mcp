---
status: accepted
date: 2026-08-12
---

# ADR-00024: Composition — a `mode` parameter, no server-side result fusion

## Context

`research_search_fetched` (and its notes equivalent) needs to expose FTS and vector search. The caller is an MCP client (an LLM agent), not a human typing into a search box — this project already leans on that distinction elsewhere (the `ResearchPublicationProvider` capability table lets a provider say it can't support a capability rather than fake it; `parse_full_text` refuses to silently trigger `fetch_full_text` to keep a decision point with the caller). An earlier draft of this design had `hybrid` perform server-side reciprocal-rank fusion (RRF) across mechanisms.

## Decision

`research_search_fetched` gains a `mode` parameter — `fts`, `vector`, and `hybrid`. `mode="hybrid"` fans out to every mechanism that exists as a capability of the running server and returns each one's results as its own separately-labeled set (`{"fts": {...}, "vector": {...}, "index_status": {...}}`), rather than merging them into one server-fused ranked list.

## Alternatives considered

- **One opaque "smart" search call, collapsing FTS/vector internally** — rejected: an agent can act on mechanism-level information the way a human search-box user can't, so collapsing that information away throws it out for no benefit.
- **Server-side reciprocal-rank fusion (RRF) in `hybrid`** (an earlier draft of this design) — reversed: RRF sidesteps raw-score incomparability (FTS5's BM25 and cosine similarity aren't on the same scale) but still requires choosing a relative *weight* per mechanism, which is genuinely query-dependent and the server has no principled way to pick. A server-fused single list is also, functionally, exactly the opaque "smart" search this decision already rejects, just reintroduced one layer later.

## Consequences

Handing back each mechanism's native results, separately labeled, keeps the one-call convenience (no need to orchestrate separate round-trips) while leaving the actual cross-mechanism synthesis judgment to the calling agent. `score` never needs to be comparable across mechanisms — each mechanism's `score` only needs to make sense within its own ranked list. An agent that wants a specific mechanism deliberately can still request it explicitly via `mode`.

## Referenced from

- [Vector search → Composition: a `mode` parameter, not a new opaque "smart" search](../search/02-vector-search.md#composition-a-mode-parameter-not-a-new-opaque-smart-search)
