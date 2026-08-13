---
status: accepted
date: 2026-08-13
---

# ADR-00028: Graph algorithms are implemented independently via NetworkX, not delegated to the engine

## Context

Whole-graph algorithms (PageRank, Louvain, Dijkstra, etc.) must run over the **override-resolved** graph — a `reject`ed edge still shaping another node's score, or a cascade-pruned node still counted in a community, would silently defeat the human veto the override layer exists to provide (see [ADR-00029](00029-human-overrides-patch-layer.md)). A live SQL view over base + override tables already solves this cleanly for `get_nodes`/`get_edges`/`neighbors` reads — always correct by construction, no sync code, no drift risk.

## Decision

Algorithms are implemented independently of the storage engine, behind a `GraphAlgorithmsBackend` interface, using [NetworkX](https://networkx.org/) as today's implementation — pulling the override-resolved node/edge set via the same live-resolution mechanism the read primitives use, building an in-memory `networkx.Graph`/`DiGraph`, and running NetworkX's own vetted implementations.

## Alternatives considered

- **Engine-native algorithm calls** (`graphqlite`'s own `pagerank`/`louvain`/`shortest_path`) — ruled out: they accept only algorithm-tuning parameters (`damping`, `iterations`, `resolution`, ..., confirmed via `inspect.signature`), and Cypher composition attempts to scope them (`MATCH ... WHERE ... CALL pageRank() ...`, `CALL pageRank() YIELD ... WHERE ...`) both fail to parse — not merely undocumented, structurally unsupported. There is no way to restrict an engine-native algorithm call to less than the whole physical graph as stored, which cannot express "override-resolved" scoping.

## Consequences

Not a `graphqlite`-specific gap — a property of delegating to any engine's built-in implementation, plausibly worse for a future hosted graph database (less storage control, likely no subgraph-scoping either, plus network cost per call). Keeps resolution singular: one live query-time step, feeding both targeted reads and algorithm bulk-export, rather than a two-mechanism hybrid a materialized-table workaround would require. Restores engine independence twice over: `GraphSearchBackend`'s engine only ever needs to answer "give me the nodes/edges," and `GraphAlgorithmsBackend`'s own NetworkX-backed implementation can later be swapped (`python-igraph`, `rustworkx`, `NetworKit`, etc. — kept on file as future candidates, not adopted now) without callers changing, since they only ever see `GraphAlgorithmsBackend`'s own method signatures, never NetworkX's.

## Referenced from

- [Graph search → Graph algorithms: implemented independently via NetworkX, not delegated to the engine](../search/03-graph-search.md#graph-algorithms-implemented-independently-via-networkx-not-delegated-to-the-engine)
