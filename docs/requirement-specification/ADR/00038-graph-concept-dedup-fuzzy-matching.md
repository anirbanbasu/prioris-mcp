---
status: accepted
date: 2026-09-20
---

# ADR-00038: Concept dedup: `find_concepts` uses fuzzy string matching (`rapidfuzz`), not exact/substring matching or embeddings

## Context

Creating a new `Concept` risks duplicating an existing one under a different label. The calling LLM has no visibility into what `Concept` nodes already exist before deciding to create one, so `find_concepts` exists to surface plausible existing candidates for the LLM to judge — the dedup decision itself is the LLM's, not the backend's.

## Decision

`find_concepts` scores every existing `Concept`'s `label`/`aliases` against the query string using `rapidfuzz` (MIT-licensed, C-extension), ranked by similarity score, most-similar first, with a generous default limit so the LLM sees more candidates rather than fewer. This is a recall-oriented candidate surfacer, not a duplicate-blocking gate.

## Alternatives considered

- **Exact or substring/`LIKE` matching** — rejected as too restrictive: misses near-identical variants (e.g. "gradient checkpointing" vs. "gradient check-pointing") that a real writer is likely to produce.
- **Embedding-based semantic matching** (via the existing `EmbeddingBackend`/`VectorSearchBackend`) — rejected: this would tie concept dedup quality to the embedding model's own semantic capability, which is weaker than the calling LLM's own judgment, and breaks down specifically for the cases that matter most — genuine synonyms in different words (e.g. "activation checkpointing" vs. "gradient checkpointing") and cross-lingual duplicates, neither of which a small local embedding model reliably captures. It would also add a `VectorSearchBackend`/`EmbeddingBackend` dependency into `GraphSearchBackend`'s write path for a capability better left to the LLM already holding the conversation.

## Consequences

`rapidfuzz` matching catches typos and near-string variants but not genuine synonyms or cross-lingual duplicates — an accepted, documented limitation, not a gap to close within this mechanism. `list_concepts` (a resource, outside this ADR's scope) exists precisely to cover that gap: an LLM suspecting a synonym or cross-lingual duplicate that fuzzy matching won't surface can browse the full concept vocabulary instead.

## Referenced from

- [Graph search → Concept dedup: `find_concepts`](../search/03-graph-search.md#concept-dedup-find_concepts-fuzzy-string-matching-not-exact-or-embedding-based)
