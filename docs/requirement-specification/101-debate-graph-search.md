---
icon: lucide/network
---

# Debate: graph search backend

**Status: to be written.** This is a placeholder so [Debate: vector search backend](100-debate-vector-search.md) has somewhere real to link to. The actual debate — and an engine decision — is deliberately deferred to its own session, kept separate from the vector-search debate so the two don't drift into one undifferentiated discussion (see [100's "interfaces stay separate" section](100-debate-vector-search.md#interfaces-stay-separate-even-if-a-future-engine-could-serve-more-than-one) for why that separation matters even where a candidate engine could technically serve more than one concern).

## What this debate is about

`index.md`'s v2 scope already names a `GraphBackend` — "cross-paper concept linking," automatic discovery of relationships between documents/notes — as future work, undesigned, alongside `VectorSearchBackend`. This document is where that gets designed: a `GraphSearchBackend` interface, its storage/query engine, and how it gets populated.

A few threads already surfaced while brainstorming vector search, worth starting from rather than re-deriving:

- **Candidate engine: LadybugDB** (an MIT-licensed rebrand of KuzuDB, `pip install ladybug`, Cypher-based, and notably bundles native full-text and vector search alongside graph queries). A real, established-history project rather than a fresh vendor claim — but its graph/Cypher capabilities, provenance-tagging support, and persistence guarantees haven't been diligenced yet, the way `sqlite-vec`'s license/persistence/maturity were in 100. Plain SQLite graph extensions were also raised as an alternative and haven't been investigated at all.
- **Multiple, structurally different input layers**, not one pluggable "extractor" swapped for another:
    - a **structural layer** derived deterministically from data the system already has (chunk/heading hierarchy, citation/reference lists) — likely pure code, not a pluggable backend at all;
    - an **LLM-based concept-extraction layer** — genuinely pluggable, model-swappable, probabilistic, mirroring how `EmbeddingBackend` relates to `VectorSearchBackend` in 100 (a `GraphExtractionBackend`-shaped interface, injected rather than baked into the storage engine);
    - a **human-authored layer** — not extraction at all, direct CRUD authorship, likely extending `index.md`'s already-named-but-undesigned future item "manual cross-document note relations... distinct from `GraphBackend`'s automatic discovery," rather than a new mechanism.
  Whatever `GraphSearchBackend` stores likely needs **provenance tagging** per node/edge (`structural` / `llm_extracted:{model,version}` / `human`) so a caller can filter/weight by trust level, and so re-running the LLM layer can cleanly replace only that layer's output for a document — the same "whole-document replace" scoping `SearchIndex.index_entries` already does, extended by provenance.
- **Whether `GraphSearchBackend` should be one of the engines LadybugDB backs**, given it already bundles FTS and vector alongside graph — contingent on the same fidelity/persistence diligence `sqlite-vec` received in 100, not yet done here.
- **Deletion cascades are structurally harder for graph than for FTS/vector.** [100](100-debate-vector-search.md#deletion-cascade-vector-is-a-plain-scoped-delete-graph-is-deferred-to-101) settled `VectorSearchBackend`'s deletion cascade as a plain scoped delete, precisely because a vector row has no structural references pointing into it from elsewhere. Graph doesn't get that for free: a node for a chunk in a deleted document could be referenced by an edge originating from a completely different document or note (a citation link, an LLM-extracted cross-document concept relation). Whether deletion should cascade to those edges, orphan them, or leave some kind of tombstone/broken-link marker is undesigned and belongs here, not in 100.

Nothing above is a decision. It's the starting point for the actual debate, captured so it isn't lost before that session happens.
