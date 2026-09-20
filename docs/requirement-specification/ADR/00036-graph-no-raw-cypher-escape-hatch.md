---
status: accepted
date: 2026-09-20
---

# ADR-00036: No raw Cypher escape hatch, for now

## Context

`GraphSearchBackend` exposes only the fixed set of typed node/edge write and read methods settled in [ADR-00032](00032-graph-engine-ladybugdb.md) through [ADR-00035](00035-graph-algorithms-networkx.md) — no method accepts a raw query string in the underlying engine's query language. This mirrors `SearchIndex` never exposing raw SQL underneath `SqliteFts5SearchIndex`.

## Decision

No raw-Cypher (or any other backend-native query language) escape hatch in `GraphSearchBackend`, for now. Every capability the interface needs gets its own typed method with typed parameters and typed return values — the node/edge write methods, `neighbors`/`find_concepts`/`subgraph`, and the `GraphAlgorithms` layer. This is explicitly reversible: if a genuine need arises for a query shape the fixed method set can't express, a raw-query escape hatch can be added later without unwinding anything already built — an additive change, not a structural one.

## Alternatives considered

- **Expose a raw Cypher passthrough method now** (e.g. `async def query(self, cypher: str, params: dict) -> list[dict]`) — rejected: a future non-Cypher remote/hosted backend (the same swappability goal ADR-00032 preserves) couldn't honor it, and it would let callers bypass every validation/invariant the typed methods enforce — existence checks on `create_edge`'s endpoints, the metadata merge-conflict policy, cascade-delete semantics — by writing directly against the engine.

## Consequences

Every new graph capability needs its own typed method addition rather than an ad-hoc query string — more upfront design work per capability, but keeps the interface swappable and every write going through the same validated path. If a real need for arbitrary querying emerges, revisiting this decision is a deliberate, visible choice (a new ADR superseding this one), not a quiet workaround.

## Referenced from

- [Graph search → No raw Cypher escape hatch](../search/03-graph-search.md#no-raw-cypher-escape-hatch-for-now)
