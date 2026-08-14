---
status: accepted
date: 2026-08-12
---

# ADR-00022: `SearchIndex` and `VectorSearchBackend` stay separate interfaces even if a future engine could serve both

## Context

`SearchIndex` (FTS5) and `VectorSearchBackend` are two distinct Python interfaces. An engine bundling both capabilities natively exists in principle (LadybugDB was raised as a candidate: native full-text search and a vector index in one).

## Decision

The two interfaces remain separate regardless of which concrete engine(s) back them. A single engine may still end up implementing both later — interface separation and implementation consolidation are different axes — but only if it earns that through the same diligence `sqlite-vec` received (verifying its FTS engine's fidelity, its vector index type and persistence guarantees, not just its license and star count).

## Alternatives considered

- **Coupling functions together behind one engine (e.g. one engine serving both full-text and vector search)** — rejected: any future hosted/remote swap of *either* piece would then require a provider supporting *all* the coupled functionality, narrowing the option space for no necessary reason.

## Consequences

None beyond the Decision above — no engine has yet earned bundled adoption through the diligence bar this ADR sets.

## Referenced from

- [Vector search → Interfaces stay separate even if a future engine could serve more than one](../search/02-vector-search.md#interfaces-stay-separate-even-if-a-future-engine-could-serve-more-than-one)
