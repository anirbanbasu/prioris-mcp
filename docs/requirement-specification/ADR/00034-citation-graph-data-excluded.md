---
status: accepted
date: 2026-08-13
---

# ADR-00034: Citation-graph data is excluded from the structural layer, for now

## Context

Citation-graph data was extensively debated for inclusion in the structural layer — first as in-document reference-list parsing resolved to local `pointer:document` nodes, then as externally-sourced OpenAlex/CORE citation adjacency (ingress/egress edges, namespaced to each source's own Work/record identifiers) refreshed on a schedule.

## Decision

The structural layer excludes citation-graph data entirely, for now — neither in-document-derived nor externally-sourced. `structural`'s `source` metadata has exactly one live value, `"internal_parse"`. Parked pending OpenAlex/CORE `ResearchPublicationProvider` support ([#33](https://github.com/anirbanbasu/prioris-mcp/issues/33), out of scope in [Discovery](../02-discovery.md#discovery-only-not-a-researchpublicationprovider-peer)) landing, at which point whether and how to bring citation adjacency into the structural layer can be reconsidered.

## Alternatives considered

- **In-document reference-list parsing**, resolving a citation to a local `pointer:document` node whenever the cited work happens to also be locally fetched — rejected: couples the citation edge's survival to an unrelated later action. Deleting the locally-fetched document cascade-deletes the `pointer:document` node and, with it, the citation edge — silently erasing the fact that the citation relationship exists at all, purely as a side effect of decluttering a local library.
- **Externally-sourced citation adjacency** (a namespaced external id like `openalex:W12345`, stable regardless of local fetch status, exempt from human annotation) — avoids that coupling and would need no reconciliation machinery at all (nothing can be orphaned if nothing can reference it, so a periodic sync could simply blind-replace) — but rejected anyway: a bare namespaced id carries no content beyond itself, no title, no abstract, nothing that helps understand a paper, which is the whole point of PriorisMCP. It only earns its keep once something can resolve the id into real information, and that support doesn't exist yet.

## Consequences

This is a scoping decision, not an open question this document is trying to resolve.

## Referenced from

- [Graph search → Citation-graph data excluded from the structural layer, for now](../search/03-graph-search.md#citation-graph-data-excluded-from-the-structural-layer-for-now)
