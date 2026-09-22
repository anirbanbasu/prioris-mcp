---
status: accepted
date: 2026-09-20
---

# ADR-00035: Graph algorithm backend: NetworkX, not rustworkx or engine-native algorithms

## Context

`GraphSearchBackend` needs algorithmic operations over the node/edge shape settled in [ADR-00033](00033-graph-node-shape-pointer-concept-tables.md)/[ADR-00034](00034-graph-edge-shape-generic-relationship-table.md) — shortest path, centrality, community/clustering, ranking — that Cypher pattern matching alone doesn't express well. These should run independently of whichever engine backs storage, matching the swappable-backend posture already established for `EmbeddingBackend`/`VectorSearchBackend`/`GraphSearchBackend` itself, rather than depending on the storage engine's own algorithm surface (if any).

## Decision

[NetworkX](https://networkx.org) operating on an in-memory subgraph materialized from a LadybugDB Cypher query result. Because the underlying relationship table is a directed `MANY_MANY` multigraph (ADR-00034), the materialized structure is a `MultiDiGraph`, not a plain `Graph`/`DiGraph`, preserving both direction and parallel edges (independently-asserted relationships between the same node pair) rather than collapsing them. Node/edge `metadata` carries over as NetworkX attribute dicts, so algorithms can filter or weight by provenance and other differentiators without any schema change.

## Alternatives considered

- **rustworkx** — Rust-backed (PyO3), materially faster than pure-Python NetworkX, with a NetworkX-like API. Rejected for now: this project's local-first, single-user scale doesn't need the performance, and NetworkX's broader algorithm coverage and larger community outweigh raw speed at this scale. The API similarity means a later swap stays low-cost if graph size or performance ever demands it.
- **Engine-native algorithms** (LadybugDB's own, or any future swapped-in backend's) — rejected: tying algorithm availability/behaviour to whichever engine currently backs `GraphSearchBackend` undermines the same backend-portability goal [ADR-00032](00032-graph-engine-ladybugdb.md) is built around. Swapping the storage engine should not mean swapping or losing algorithm capability.

## Consequences

`GraphSearchBackend` needs a materialization step translating a Cypher query result (or a bounded subgraph fetch) into a NetworkX `MultiDiGraph` before running any algorithm — real translation work, not free engine-side compute — and algorithms operate on that snapshot, not live against the store. Scoping which subgraph gets materialized (a neighborhood vs. the whole graph) matters for cost, since pure-Python NetworkX traversal is the slower end of what's available; if that ever becomes a real bottleneck, rustworkx's close API parity keeps the swap cheap.

## Referenced from

- [Graph search → Graph algorithm backend: NetworkX](../search/03-graph-search.md#graph-algorithm-backend-networkx)
