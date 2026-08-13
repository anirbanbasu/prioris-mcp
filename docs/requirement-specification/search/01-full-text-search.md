---
icon: lucide/text-search
---

# Full-text search

**v1.** `SearchIndex` is PriorisMCP's literal/lexical search over persisted document/note content — see [Architecture → `SearchIndex`](../01-architecture.md#searchindex) for why it's a separate interface from [`StorageBackend`](../storage/01-document-storage.md#storagebackend) rather than one of its operations, not covered again here. [Vector search](02-vector-search.md) covers the embedding-based mechanism search grows into beyond keyword matching, exposed alongside this one through the same tool, `research_search_fetched`'s `mode` parameter.

## `SearchIndex`

### Interface

| Operation | Description |
|---|---|
| `index_entries` | Replace all indexed entries for a `(provider, identifier, format)` document with a fresh list — a whole-document replace, not an incremental upsert, since a parse pass produces the complete manifest fresh each time. |
| `remove_document` | Remove every indexed entry for a `(provider, identifier, format)` document. |
| `search` | Full-text search, ranked by relevance, optionally scoped to provider/identifier/format. |

The indexed unit is a [manifest](../storage/01-document-storage.md#per-document-structure-manifestsqlite-replaces-structurejsonl) `chunk` row (or a `leaf` row, for a document with none — the same leaf-fallback principle chunk-based callers apply generally), not a whole `markdown` artefact: a match is a specific section or page, not merely "this document contains the term somewhere." Like `StorageBackend`, its inputs/outputs are plain `dict`s, not Pydantic models — Pydantic is reserved for the MCP tool/resource wire boundary (see [Interface specification](../06-interface-specification.md)), and `SearchIndex` sits at the same internal-backend layer as `StorageBackend`, not that boundary.

## v1: SQLite + FTS5

v1 ships one `SearchIndex` implementation, `SqliteFts5SearchIndex`, backed by the SQLite + FTS5 index at `search.sqlite3` — see [Storage → Full-text search](../storage/01-document-storage.md#full-text-search-the-searchsqlite3-index) for the index's physical layout, sync model, and its relationship to `catalogue.sqlite`/`manifest.sqlite`.
