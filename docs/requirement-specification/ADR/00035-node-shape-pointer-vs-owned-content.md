---
status: accepted
date: 2026-08-13
---

# ADR-00035: Node shape: pointer vs. owned content, no separate `external_reference` kind or namespaced-id discriminator

## Context

Structural nodes (chunks, headings, documents, notes) don't need to own content — `VectorSearchBackend`, `NotesBackend`, and the document store already own it. Duplicating any of that into a second, graph-owned copy would re-raise the same problem the override design already resolved once by rejecting `promoted_from`'s content-copying (see [ADR-00029](00029-human-overrides-patch-layer.md)). Concept nodes, by contrast, are cross-cutting by nature and do need owned content.

## Decision

Two disjoint field groups, not three node kinds: a node is either a **pointer** (`ref_type`: `"chunk"`/`"note"`/`"document"` plus `ref_id`) or **owned-content** (`content`), fully recoverable from which fields are populated — no explicit `kind`/discriminator field needed at all.

## Alternatives considered

- **A third node kind, `external_reference`**, sitting alongside `pointer` and `concept` — motivated by citations to works not (yet) locally fetched, with nothing for a pointer to reference. Collapsed into `concept` instead: an external reference turns out to have no content shape distinct from a concept's (both are "a label, owned because nothing else owns it") — what actually distinguishes a citation-derived entry from an abstract topic is **provenance**, not content shape.
- **Folding the pointer/owned-content discriminator into a namespaced `id`** (e.g. `"chunk:<id>"` vs. a bare concept id) — rejected as the same overloaded-field mistake already rejected once for provenance (the original `"llm_extracted:{model}:{version}"` string): cramming "which kind of thing this is" and "which specific thing it is" into one field is unparseable at the schema level and can't carry a real foreign key back into `chunks`/`notes`/`documents`.

## Consequences

`id` stays opaque; `ref_type` stays its own explicit, indexable column. The general pointer/owned-content shape stands for `llm_extracted`/`human_annotated` concepts even though, with citation-graph data excluded from the structural layer ([ADR-00034](00034-citation-graph-data-excluded.md)), no `structural`-provenance concept node exists today.

## Referenced from

- [Graph search → Node shape: pointer vs. owned content, without a separate "external reference" kind](../search/03-graph-search.md#node-shape-pointer-vs-owned-content-without-a-separate-external-reference-kind)
