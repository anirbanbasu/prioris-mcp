---
status: accepted
date: 2026-08-12
---

# ADR-00019: Semantic Scholar's similarity capability — placement, not adoption

## Context

Semantic Scholar's `/paper/search` (and bulk search) is keyword search against an Elasticsearch index with a custom relevance re-ranker — not free-text embedding search, so it isn't a `search.semantic` peer (see [ADR-00015](00015-discovery-openalex-scope-not-multi-aggregator.md)). It does compute SPECTER2 embeddings per paper, and the Recommendations API built on them takes a **seed paper ID** and returns similar papers — a citation/embedding-graph-flavored retrieval mechanism, which raises the question of whether it belongs with [Graph search](../search/03-graph-search.md)'s `GraphSearchBackend` instead of discovery.

## Decision

If Semantic Scholar's Recommendations API is ever adopted, it belongs alongside `research_discovery` (a candidate future extension, in the same tier as CORE), not folded into `GraphSearchBackend`.

## Alternatives considered

- **Treating it as part of `GraphSearchBackend`, since its retrieval mechanism is citation/embedding-graph-flavored** — rejected: the boundary between discovery and graph search is drawn by which corpus a mechanism operates over — already-fetched local corpus (graph search) vs. not-yet-fetched external catalogue (discovery) — not by what kind of algorithm produced a result. Recommendations can return papers the local corpus has never seen, seeded by a paper ID instead of free text, which makes it a discovery mechanism regardless of its citation-graph-flavored implementation.

## Consequences

Graph search has independently settled the corpus-scope boundary from its own side: citation-graph data (from Semantic Scholar, OpenAlex, or CORE) is excluded from `GraphSearchBackend`'s structural layer entirely, for now ([Graph search § Citation-graph data excluded from the structural layer, for now](../search/03-graph-search.md#citation-graph-data-excluded-from-the-structural-layer-for-now)) — a bare id-to-id citation pointer carries no content beyond itself, and only earns its keep once OpenAlex/CORE `ResearchPublicationProvider` support ([#33](https://github.com/anirbanbasu/prioris-mcp/issues/33)) exists to resolve it into something useful.

## Referenced from

- [Discovery → Semantic Scholar's similarity capability](../02-discovery.md#semantic-scholars-similarity-capability-split-by-corpus-scope-not-retrieval-shape)
