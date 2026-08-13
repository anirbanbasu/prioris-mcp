---
status: accepted
date: 2026-07-20
---

# ADR-00011: One global search index, not one per document

## Context

`search.sqlite3` could be a single corpus-wide index or a separate index per document, and search could be backed by a persistent index or an on-the-fly scan.

## Decision

One global `search.sqlite3`, not one index per document. A query can be scoped to a single document (`MATCH ... AND identifier = ?`) or left unscoped, through the same table.

## Alternatives considered

- **A single-document-scoped index (a separate index per document)** — rejected: BM25's IDF term is only meaningful across multiple documents in the first place (a one-document corpus has no document-frequency contrast to compute it from), so a genuinely per-document index couldn't produce more meaningful ranking than the scoped-`WHERE`-over-the-global-index approach — it would only add file-proliferation and per-file lock-contention cost for no ranking benefit.
- **No persistent index at all for "search within one already-fetched document"** — rejected as unnecessary, not wrong: an in-memory document's Markdown is small enough that a caller can substring/regex-scan it directly. The FTS5 index exists to avoid the genuinely O(n)-in-corpus-size scan that cross-document search would otherwise require at this store's target scale (tens of thousands of documents) — a concern a `WHERE`-scoped query over the global index doesn't reintroduce, since FTS5 resolves `MATCH` through the inverted index's posting lists regardless of how many documents are indexed.

## Consequences

Document-scoped and corpus-wide search share one index and one code path. Overlapping matches (a broad section and one of its nested subsections both matching) are an accepted characteristic of chunk-based indexing, not a defect.

## Referenced from

- [Storage → Full-text search: the `search.sqlite3` index](../storage/01-document-storage.md#full-text-search-the-searchsqlite3-index)
