---
status: accepted
date: 2026-09-20
---

# ADR-00039: Algorithm surface: seven fixed methods, not `shortest_path` alone and not the full NetworkX catalog

## Context

[ADR-00035](00035-graph-algorithms-networkx.md) settled NetworkX as the engine-independent library for graph algorithms, but not *which* algorithms `GraphAlgorithms` exposes. NetworkX's own algorithm reference spans 30+ families (bipartite, planarity, chordal, matching, tournament, ...), most with no plausible use case over a `Pointer`/`Concept` prior-art graph.

## Decision

A fixed, named set of seven methods — `betweenness_centrality`, `pagerank`, `communities` (Louvain), `paths`, `reachable`, `steiner_tree`, `predict_links` (Adamic-Adar) — each backed by a concrete prior-art use case, not a generic passthrough to arbitrary NetworkX functions. Extending this set later means adding a new named method, the same reversible, additive posture as [ADR-00036](00036-graph-no-raw-cypher-escape-hatch.md)'s "no raw Cypher" decision.

## Alternatives considered

- **A single `shortest_path` method** returning only the one optimal route between two nodes — rejected: collapses to one "best" connection and discards other paths that may carry independently valuable semantic information for prior-art discovery. Replaced by two methods that don't optimise away alternatives: `paths` (`all_simple_paths`, cutoff-bounded, sorted shortest-first before truncating to `max_paths` — so ordering is preserved without discarding non-shortest routes) and `reachable` (`descendants`/`ancestors`, depth-bounded), which answers open-ended "what does this connect to" without requiring a specific target at all.
- **A generic `run_algorithm(name: str, **kwargs)` passthrough** covering the full NetworkX catalog — rejected for the same reason as ADR-00036's raw-Cypher rejection: an unbounded surface isn't a stable, reviewable interface contract, and almost none of NetworkX's 30+ families have a plausible use case here.
- **`greedy_modularity_communities` or `girvan_newman`** for community detection, instead of Louvain — rejected: `greedy_modularity_communities` is an older, slower heuristic; `girvan_newman` (edge-betweenness-based) is expensive and doesn't scale. Louvain is the modern standard, ships in NetworkX core (no extra dependency), and is fast enough at this project's scale.
- **Plain Jaccard coefficient or preferential attachment** for link prediction, instead of Adamic-Adar — rejected: neither down-weights high-degree "hub" concepts (e.g. "machine learning") the way Adamic-Adar does, which matters in a concept graph likely to have a few such hubs; Adamic-Adar is also the standard baseline in citation-network link prediction, a close domain match.

## Consequences

Every one of the seven methods is depth-bounded (`max_depth`/`depth` off an explicit seed node set, or a pair for `paths`) and most accept a `relation_type` filter via `subgraph()` — unbounded whole-graph algorithm runs are never exposed, keeping cost predictable regardless of corpus size. `steiner_tree`'s NetworkX implementation is a polynomial-time 2-approximation (exact Steiner tree is NP-hard) — documented as an approximation, not an optimal result. `predict_links` only scores pairs already co-located within the materialized neighbourhood, not the whole graph — a real scoping limitation, not a global suggestion engine.

## Referenced from

- [Graph search → Algorithm surface: seven fixed methods](../search/03-graph-search.md#algorithm-surface-seven-fixed-methods-not-shortest_path-alone-and-not-the-full-networkx-catalog)
