---
status: accepted
date: 2026-08-12
---

# ADR-00022: `SearchIndex`, `VectorSearchBackend`, and `GraphSearchBackend` stay separate interfaces even if a future engine could serve more than one

## Context

`SearchIndex` (FTS5), `VectorSearchBackend`, and `GraphSearchBackend` (see [Graph search](../search/03-graph-search.md)) are three distinct Python interfaces. A single engine bundling more than one of these capabilities natively exists in principle (LadybugDB was raised as a candidate: native full-text search, a vector index, and graph queries all in one).

## Decision

The three interfaces remain separate regardless of which concrete engine(s) back them. A single engine may still end up implementing more than one of these interfaces later — interface separation and implementation consolidation are different axes — but only if it earns that through the same diligence `sqlite-vec` received (verifying its FTS engine's fidelity, its vector index type and persistence guarantees, not just its license and star count).

## Alternatives considered

- **Coupling functions together behind one engine (e.g. one engine serving both full-text and vector search)** — rejected: any future hosted/remote swap of *either* piece would then require a provider supporting *all* the coupled functionality, narrowing the option space for no necessary reason.

## Consequences

That diligence has since happened in [Graph search](../search/03-graph-search.md)'s own investigation, which surfaced real governance risk in the LadybugDB/Kuzu-fork landscape and settled the matter by elimination: `graphqlite` turned out to be Cypher-only, with no FTS/vector capability to share.

## Referenced from

- [Vector search → Interfaces stay separate even if a future engine could serve more than one](../search/02-vector-search.md#interfaces-stay-separate-even-if-a-future-engine-could-serve-more-than-one)
