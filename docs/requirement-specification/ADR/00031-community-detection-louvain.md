---
status: accepted
date: 2026-08-13
---

# ADR-00031: Community detection: `louvain`, not `greedy_modularity_communities` or label propagation

## Context

The algorithm catalogue needed community detection for topic-group clustering. At 50,000-node/500,000-edge scale, `louvain` measured 13.7–14.8s — expensive enough to need the async/progress-reporting tier (see [Graph search → Expensive-tier algorithms](../search/03-graph-search.md#expensive-tier-algorithms-louvain-and-approximate-betweenness_centrality-run-as-long-running-calls-with-progress-reporting-not-ordinary-synchronous-tool-calls)).

## Decision

`louvain` stays the settled choice for community detection, kept on the async/progress-reporting tier rather than replaced.

## Alternatives considered

- **`greedy_modularity_communities` (CNM)** — still running after 10+ minutes of CPU time at 50,000/500,000 scale; killed and ruled out on wall-clock alone, neither faster nor (as far as it got) better than `louvain`.
- **Label propagation** (both NetworkX variants) — genuinely fast (semi-synchronous: 1.14s; asynchronous: 4.07s) but qualitatively broken on the test graph, not just lower-quality: the semi-synchronous variant collapsed to a single community covering all 50,000 nodes (modularity 0.0), the asynchronous variant fragmented into 13,120 mostly-tiny communities (modularity 0.127, against `louvain`'s 0.211) — consistent with label propagation's known literature reputation for instability on weakly-clustered graphs.

## Consequences

`louvain` is relocated to the expensive/async tier purely because it's inherently this expensive at this scale, not because a tunable is being deliberately withheld: NetworkX's `max_level` parameter was tested at 50,000/500,000 scale and found to behave as a cliff, not a dial (`max_level=1` gave a real speedup at the cost of much coarser communities; `max_level=3` was barely different from uncapped), so it wasn't adopted as a user-facing parameter. NetworkX's `backend="parallel"` dispatch also doesn't cover `louvain_communities` at all.

## Referenced from

- [Graph search → Expensive-tier algorithms: `louvain` and approximate `betweenness_centrality`, run as long-running calls with progress reporting, not ordinary synchronous tool calls](../search/03-graph-search.md#expensive-tier-algorithms-louvain-and-approximate-betweenness_centrality-run-as-long-running-calls-with-progress-reporting-not-ordinary-synchronous-tool-calls)
