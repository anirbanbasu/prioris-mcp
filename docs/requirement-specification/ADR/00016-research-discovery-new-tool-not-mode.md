---
status: accepted
date: 2026-08-12
---

# ADR-00016: `research_discovery` is a new tool, not a `mode` on `research_search_fetched`

## Context

[Vector search](../search/02-vector-search.md) already settles `research_search_fetched` exposing `fts`/`vector`/`graph`/`hybrid` via a single `mode` parameter — one tool, several retrieval mechanisms, all sharing one domain: local corpus content already fetched into storage, returning a consistent chunk/doc-shaped result. `search.semantic` candidates need somewhere to surface too.

## Decision

Discovery gets its own tool, `research_discovery`, mirroring `research_arxiv_search`/`research_europepmc_search` in naming — but unlike those two, it has no `ResearchPublicationProvider` behind it; it's a capability layer sitting in front of providers, not a provider itself.

## Alternatives considered

- **A new `mode` value on `research_search_fetched`** — rejected: `search.semantic` candidates don't share `research_search_fetched`'s domain. They're external, not-yet-fetched works — title/abstract/authors/OA-pointer metadata, no `chunk_id`, no guarantee they'll ever be fetched. Folding them into the existing `mode` enum would force one result union to carry two structurally incompatible shapes.

## Consequences

`research_discovery` has no `ResearchPublicationProvider` behind it unless/until [#33](https://github.com/anirbanbasu/prioris-mcp/issues/33) changes that.

## Referenced from

- [Discovery → Surfacing shape](../02-discovery.md#surfacing-shape-a-new-tool-research_discovery-not-a-mode-on-research_search_fetched)
