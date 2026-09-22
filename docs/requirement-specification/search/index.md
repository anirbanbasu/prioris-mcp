---
icon: lucide/search
---

# Search

PriorisMCP exposes retrieval mechanisms over content already fetched into its storage, covered as separate chapters in this section:

- [Full-text search](01-full-text-search.md) — `SearchIndex`, literal/lexical keyword matching over persisted chunks/leaves (v1).
- [Vector search](02-vector-search.md) — `VectorSearchBackend`, embedding-based semantic search over the same corpus (v3).
- [Graph search](03-graph-search.md) — `GraphSearchBackend`, a single-layer knowledge graph over `Pointer`/`Concept` nodes, plus a NetworkX algorithm layer on top.

Full-text and vector search share one tool, `research_search_fetched`'s `mode` parameter (see [Vector search → Composition](02-vector-search.md#composition-a-mode-parameter-not-a-new-opaque-smart-search)), because both dispatch to the same contract: a query string in, a uniform ranked-matches list out. Graph search does not share that contract — `get_node` returns one node, `neighbors` returns edge/node pairs, `subgraph` returns a whole node/edge set — so it is exposed as its own three tools (`research_graph_write`/`research_graph_query`/`research_graph_analyze`) instead, not a fourth `mode` value — see [Graph search → `research_graph_*` are new tools, not a `mode` on `research_search_fetched`](03-graph-search.md#research_graph_-are-new-tools-not-a-mode-on-research_search_fetched).

Not to be confused with [Discovery](../02-discovery.md): all three mechanisms above operate over content *already fetched* into this project's storage. Discovery is a remote, third-party mechanism for finding candidates *not yet* fetched at all — see [Discovery](../02-discovery.md) for the exact boundary.
