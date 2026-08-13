---
status: accepted
date: 2026-08-12
---

# ADR-00026: Chunk identity is a UUID minted per parse pass, not derived from heading text or position

## Context

Each detected chunk needs an identity (`chunk_id`) for `VectorSearchBackend` to tag sub-splits and collapse hits back to their parent chunk (see [Vector search → Chunking granularity](../search/02-vector-search.md#chunking-granularity-heading-bounded-split-only-when-oversized)).

## Decision

`chunk_id = str(uuid.uuid4())`, generated fresh during chunk detection on every parse pass.

## Alternatives considered

- **The chunk's heading text** — rejected: not guaranteed unique (two sections in one document can share a heading, e.g. two "Introduction"s in a multi-study paper).
- **The chunk's character offset** — rejected: drifts on any incidental upstream re-parse difference (e.g. a parser-library upgrade), even when the section's actual content didn't change.

## Consequences

`chunk_id` doesn't need to be human-readable or stable *across* parse passes over time — only *within* one: `SearchIndex.index_entries`'s existing implementation already deletes every prior entry for a document **by document identity** before inserting the new set, never by matching individual entry keys across passes, and `VectorSearchBackend`'s and the future `GraphSearchBackend`'s document-replace operations must follow the identical pattern. Freshly-minted `chunk_id`s on every parse pass are therefore safe by construction — old entries are always fully cleared before new ones are written, so there's no reconciliation step that could fail.

## Referenced from

- [Vector search → Chunk identity: a UUID minted per parse pass, not derived from heading text or position](../search/02-vector-search.md#chunk-identity-a-uuid-minted-per-parse-pass-not-derived-from-heading-text-or-position)
