---
status: accepted
date: 2026-07-20
---

# ADR-00004: `SearchIndex` as a separate interface from `StorageBackend`

## Context

Full-text search needs to live somewhere in the architecture: either as an operation on [`StorageBackend`](../storage/01-document-storage.md#storagebackend), or as its own interface.

## Decision

`SearchIndex` is its own interface, independent of `StorageBackend`, with v1's one implementation (`SqliteFts5SearchIndex`) covered in full in [Search](../search/index.md).

## Alternatives considered

- **A `search` method on `StorageBackend` itself** — rejected: an S3-backed `StorageBackend` implementation's `search` wouldn't touch S3 at all — it would have to delegate to whatever indexing infrastructure a `SearchIndex` implementation actually uses (SQLite/FTS5, a hosted search service, ...), infrastructure unrelated to `StorageBackend`'s other operations regardless of which concrete `SearchIndex` ends up configured. A method implemented against infrastructure unrelated to the rest of its interface is a standard signal it belongs on its own interface instead.

## Consequences

`StorageBackend` and `SearchIndex` can be swapped independently — a future S3-backed `StorageBackend` needs no search-specific changes, and vice versa. This is the same seam later reused to justify `VectorSearchBackend` and `GraphSearchBackend` as their own interfaces too, rather than folding search variants into `StorageBackend` or into each other — see [Vector search → Interfaces stay separate](../search/02-vector-search.md#interfaces-stay-separate-even-if-a-future-engine-could-serve-more-than-one).

## Referenced from

- [Architecture → `SearchIndex`](../01-architecture.md#searchindex)
