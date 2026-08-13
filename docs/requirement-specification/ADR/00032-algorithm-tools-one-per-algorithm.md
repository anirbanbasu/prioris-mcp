---
status: accepted
date: 2026-08-13
---

# ADR-00032: MCP tool wiring for algorithms: one tool per algorithm, not a dispatcher

## Context

`GraphAlgorithmsBackend`'s 14-algorithm catalogue needs MCP tool wiring. Its parameters are genuinely heterogeneous across algorithms (`k`, `resolution`/`seed`, `alpha`, `candidate_pairs` with no `node_ids` at all, `source_id`/`target_id`).

## Decision

Fourteen separate tools, `research_graph_<algorithm_name>` (`research_graph_pagerank`, `research_graph_louvain`, `research_graph_dijkstra`, ...), each with its own static, typed JSON schema — no extra "algo" infix, matching `research_graph_status`'s own naming, same `ResearchPublicationProvider`-scoped prefix reasoning as the write primitives.

## Alternatives considered

- **A single `research_graph_run_algorithm(name, node_ids, **params)`-style dispatcher** — considered mainly to keep the tool count down, rejected: FastMCP derives one static, typed JSON schema per tool, which is exactly what makes `k`'s `Field(ge=1, le=100)` bound and `dijkstra`'s always-present-but-nullable return shape work. A dispatcher would need either an unvalidated params blob (losing those bounds) or an awkward discriminated-union input shape — harder for a calling agent to construct correctly than 14 narrowly-typed, self-documenting tools.

## Consequences

`louvain`/`betweenness_centrality` need nothing structurally different at the registration level for being on the async/progress tier — that's a behavioural distinction inside the tool body (calling `ctx.report_progress`), not a different tool shape.

## Referenced from

- [Graph search → Algorithm signatures: shared `node_ids` scoping, bounded parameters, and configurability](../search/03-graph-search.md#algorithm-signatures-shared-node_ids-scoping-bounded-parameters-and-configurability)
