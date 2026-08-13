---
status: accepted
date: 2026-08-12
---

# ADR-00020: Corpus topology — two corpus-wide `VectorSearchBackend` instances, not one unified index and not per-document

## Context

The concrete use case [Architecture → Anchoring](../01-architecture.md#anchoring-document-level-not-span-level) already named — "similarity search between a note's text and the chunk currently being read finds relevant notes" — is a **directional** query (a chunk's embedding used to search the notes corpus), not a merged ranked list spanning both document chunks and notes.

## Decision

One `VectorSearchBackend` instance covers all document chunks across every fetched document; a second covers all notes across every document — mirroring `search.sqlite3`/`notes-search.sqlite3`, which are each already single, corpus-wide files rather than one per document. Cross-type queries (chunk → relevant notes) are handled by querying the notes-corpus instance with a chunk-derived embedding, optionally filtered by `provider`/`identifier`/`format`.

## Alternatives considered

- **A single unified, type-tagged table spanning both chunks and notes** — rejected: the directional nature of the actual use case removes the main argument for a unified table in the first place; there's no genuine need to rank chunks and notes against each other in one list.
- **Per-document (or per-document-plus-its-notes) scoping** — rejected: it would regress the cross-document notes search `NotesBackend` already shipped via FTS5, since notes attached to different papers would no longer be semantically searchable together.

## Consequences

Filtering, not separate storage, is how a caller narrows to "notes on this specific document." "Corpus-wide" here implicitly means *within one provider grouping* (`ResearchPublicationProvider`, the only one that exists) — see [Architecture → Provider groupings](../01-architecture.md#provider-groupings) for the constructor-level seam a second grouping would attach through.

## Referenced from

- [Vector search → Corpus topology](../search/02-vector-search.md#corpus-topology-two-corpus-wide-instances-not-one-unified-index-and-not-per-document)
