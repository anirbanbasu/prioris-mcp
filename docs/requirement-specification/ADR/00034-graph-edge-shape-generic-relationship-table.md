---
status: accepted
date: 2026-09-20
---

# ADR-00034: Edge shape: one generic `MANY_MANY` relationship table, free-text `relation_type`

## Context

With `Pointer` and `Concept` as the two node tables ([ADR-00033](00033-graph-node-shape-pointer-concept-tables.md)), edges need a shape letting any human- or agent-asserted relationship connect any combination of them, without the schema constraining relation vocabulary or how many relationships can exist between a given pair. Every node/edge is written only through the same MCP write path a human or an agent-on-behalf-of-human uses — nothing is auto-derived by an in-server extraction pass. That doesn't rule out *partially auto-derived* edges (e.g. an OpenAlex citation-derived link, or an LLM-suggested concept relation): those are just ordinary edges whose `metadata.provenance` records where they came from, written through the same path as everything else, with human review being a plain update (edit `metadata`, adjust `weight`) or delete on that same row — no override/patch layer reconciling separate strata.

## Decision

One relationship table, declared across all four `Pointer`/`Concept` `FROM`/`TO` combinations, left at LadybugDB's default `MANY_MANY` multiplicity. Columns: `id` (server-minted UUID), `relation_type` (free text), `weight` (optional numeric), `metadata` (open `MAP`), `created_at`, `updated_at`.

No node-pair uniqueness is imposed: LadybugDB relationship identity is an internally-generated edge id, not `(source, target, relation_type)`, so multiple edges — even sharing the same `relation_type` — between the same pair of nodes are natively supported (a multigraph). Independently-authored relationships (human vs. LLM-suggested, or two different `relation_type` labels for what two different actors consider the same connection) coexist rather than colliding.

## Alternatives considered

- **Per-relation-type or multiplicity-restricted relationship tables** (e.g. a `MANY_ONE` table for a hierarchical-feeling relation) — rejected for now: no concrete relation currently needs a hard multiplicity guarantee, and even if one arose it fits inside the generic `MANY_MANY` table as a convention rather than a schema constraint. Enforcing it at the schema level would tie correctness to a LadybugDB-specific declarative feature a future swapped-in remote backend might not have; an application-level check in `GraphSearchBackend`'s own write path travels with the interface regardless of engine.
- **A dedicated `aliases`-style field on edges**, mirroring `Concept.aliases` — rejected: an edge isn't referenced by other parts of the graph the way a concept is, so there's no analogous "many names, one canonical thing to point at" problem. The real risk — `relation_type` vocabulary fragmenting (`"cites"` vs. `"references"`) — is absorbed by allowing multiple parallel edges rather than merging labels onto one row.
- **A dedicated, typed `confidence` column** — deferred: no current writer for it, since no auto-derivation pipeline is designed yet, and it's the same shape of qualifier as provenance. It lives in `metadata` for now, promotable to a dedicated column later if query/filter ergonomics demand it once a real source exists.

## Consequences

`relation_type` vocabulary is entirely free-form and can fragment (multiple labels meaning roughly the same thing) — accepted as the cost of not gatekeeping connectivity. A future normalization aid (e.g. a resource listing `relation_type` values already in use, so a writer can reuse rather than reinvent one) is left as an interface-design concern, not a schema one. Because relationships carry no schema-level identity beyond an internal edge id, `GraphSearchBackend`'s own read/update/delete operations must address edges by their own minted `id` property, mirroring the convention already used for nodes.

## Referenced from

- [Graph search → Edge shape: one generic relationship table](../search/03-graph-search.md#edge-shape-one-generic-relationship-table)
