---
status: accepted
date: 2026-09-20
---

# ADR-00033: Node shape: two typed tables, `Pointer` and `Concept`, kind is table membership, not a field

## Context

`GraphSearchBackend`'s single-layer graph (see [ADR-00032](00032-graph-engine-ladybugdb.md) for the engine, and the property that differentiators like provenance live in open metadata rather than driving separate storage layers) needs a concrete node shape against LadybugDB's schema-required property-graph model. Two distinct node purposes fall out of what the graph is for: linking something that already has a home elsewhere in PriorisMCP (a document, a chunk, a note), and representing something that doesn't (a concept, method, topic, or other standalone entity).

## Decision

Two node tables:

- **`Pointer`** — `ref_type` (`chunk`/`document`/`note`), `ref_id` (the referenced entity's existing id). Unique on `(ref_type, ref_id)`: a write targeting an existing pair upserts onto the same node rather than creating a duplicate, so multiple concepts linking to the same chunk converge on one `Pointer` node instead of fragmenting across several.
- **`Concept`** — `label` (required), `aliases` (list of alternate names, a deduplication aid at write time), `description` (optional, freeform).
- Common to both, declared columns: `id` (server-minted UUID, not LadybugDB's internal edge/node id — the same external-id convention used elsewhere in this codebase), `created_at`, `updated_at`.
- Common to both, open: `metadata` (`MAP`) — provenance and any other differentiator or qualifier.

No `kind` discriminator field exists anywhere: which table a node lives in already answers "pointer or concept."

## Alternatives considered

- **A single node table** with nullable `ref_type`/`ref_id`/`label`/`description` columns plus a `kind` discriminator — rejected: LadybugDB's schema-required model expresses the two shapes as two tables with no wasted nullable columns and no discriminator field to keep in sync with which columns are actually populated; table membership is a free, structurally-enforced discriminator instead.
- **A separate `descriptors` field** (`[{kind, value}]` qualifiers, distinct from `metadata`) — superseded: concept name-variant needs are covered by `aliases`, and every other qualifying/differentiating need (provenance, confidence, source) fits the open `metadata` bag. A third structure earns no distinct job.
- **Minting a fresh `Pointer` node on every write** instead of upserting on `(ref_type, ref_id)` — rejected: would let the same chunk/document/note accumulate duplicate nodes across separate linking actions over time, fragmenting `neighbors()` results for what is conceptually one thing.

## Consequences

`Pointer` nodes never own content — resolving a pointer's actual text/title means joining out to `StorageBackend`/`NotesBackend` by `ref_id` at read time, keeping the graph cheap to store and immune to going stale relative to the source content it references. `Concept` nodes are the graph's only owned content, and need enough of it (`label` plus `description`) to be useful to an LLM traversing the graph without a second lookup. `aliases` helps a writer recognize that a new concept ("activation checkpointing") is the same as an existing one ("gradient checkpointing"), but doesn't by itself prevent duplicate concept creation — that responsibility sits with whatever write-time matching logic the interface specification (still to come) defines.

## Referenced from

- [Graph search → Node shape: two typed tables, `Pointer` and `Concept`](../search/03-graph-search.md#node-shape-two-typed-tables-pointer-and-concept)
