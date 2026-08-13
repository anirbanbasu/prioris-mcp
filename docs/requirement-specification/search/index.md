---
icon: lucide/search
---

# Search

PriorisMCP exposes retrieval mechanisms over content already fetched into its storage, all through one tool, `research_search_fetched`'s `mode` parameter (see [Vector search → Composition](02-vector-search.md#composition-a-mode-parameter-not-a-new-opaque-smart-search)), covered as separate chapters in this section:

- [Full-text search](01-full-text-search.md) — `SearchIndex`, literal/lexical keyword matching over persisted chunks/leaves (v1).
- [Vector search](02-vector-search.md) — `VectorSearchBackend`, embedding-based semantic search over the same corpus (v3).
- [Graph search](03-graph-search.md) — `GraphSearchBackend`, cross-document concept/citation traversal, on a `graphqlite`-backed SQLite engine (v3).

Not to be confused with [Discovery](../02-discovery.md): all three mechanisms above operate over content *already fetched* into this project's storage. Discovery is a remote, third-party mechanism for finding candidates *not yet* fetched at all — see [Discovery](../02-discovery.md) for the exact boundary.
