---
status: accepted
date: 2026-08-13
---

# ADR-00030: Link prediction: `preferential_attachment`, not Jaccard/Adamic-Adar/`resource_allocation_index`

## Context

The algorithm catalogue needed a link-prediction entry to surface "structural holes" — two concepts/papers sharing many neighbors but not directly connected, a candidate for novel synthesis.

## Decision

`preferential_attachment`, invoked only over an explicit, bounded candidate-pair set supplied by the caller (e.g. a query node's 2-hop non-adjacent neighborhood), never scored over every non-adjacent pair.

## Alternatives considered

- **Jaccard coefficient and Adamic-Adar index** — the original candidates, shared-neighborhood-based, framed around the structural-holes/synthesis question directly. Both tested and work correctly, but slower: 87.08ms and 48.01ms respectively vs. `preferential_attachment`'s 13.85ms, scored over the same 18,864-pair candidate set at 50,000-node/500,000-edge scale.
- **`resource_allocation_index`** — tested as a closer-to-original-intent alternative (same complexity class as Adamic-Adar, 47.81ms, different neighbor-weighting) but not faster; not adopted.

## Consequences

`preferential_attachment` is a real framing change, not just a faster drop-in: it scores likelihood by node popularity/degree, not shared neighborhood, so it answers "which well-connected nodes are likely to gain a new edge," not the original structural-hole/synthesis-opportunity question Jaccard/Adamic-Adar were chosen to answer. Scoring every non-adjacent pair isn't viable at any real scale (~1.25 billion pairs at 50,000 nodes), so the bounded-candidate-set requirement is load-bearing, not optional — cost tracks candidate-set size, not corpus size (sub-2ms for a single query node's ~400-pair neighborhood, 13.85ms for 50 query nodes' combined ~18,864-pair neighborhood, both at 50,000-node/500,000-edge scale).

## Referenced from

- [Graph search → Graph algorithms: implemented independently via NetworkX, not delegated to the engine](../search/03-graph-search.md#graph-algorithms-implemented-independently-via-networkx-not-delegated-to-the-engine)
