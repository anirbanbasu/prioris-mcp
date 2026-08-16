---
status: accepted
date: 2026-08-12
---

# ADR-00019: Semantic Scholar's similarity capability — placement, not adoption

## Context

Semantic Scholar's `/paper/search` (and bulk search) is keyword search against an Elasticsearch index with a custom relevance re-ranker — not free-text embedding search, so it isn't a `search.semantic` peer (see [ADR-00015](00015-discovery-openalex-scope-not-multi-aggregator.md)). It does compute SPECTER2 embeddings per paper, and the Recommendations API built on them takes a **seed paper ID** and returns similar papers — a citation/embedding-graph-flavored retrieval mechanism, which raises a corpus-scope placement question: discovery, or a locally-scoped citation/concept-linking mechanism instead.

## Decision

If Semantic Scholar's Recommendations API is ever adopted, it belongs alongside `research_discovery` (a candidate future extension, in the same tier as CORE), not folded into a locally-scoped citation/concept-linking mechanism.

## Alternatives considered

- **Treating it as part of a locally-scoped citation/concept-linking mechanism, since its retrieval mechanism is citation/embedding-graph-flavored** — rejected: the boundary is drawn by which corpus a mechanism operates over — already-fetched local corpus vs. not-yet-fetched external catalogue (discovery) — not by what kind of algorithm produced a result. Recommendations can return papers the local corpus has never seen, seeded by a paper ID instead of free text, which makes it a discovery mechanism regardless of its citation-graph-flavored implementation.

## Consequences

None beyond the Decision above: Semantic Scholar's Recommendations API remains unadopted either way; this ADR only settles where it would land if and when adoption is revisited.

## Referenced from

- [Discovery → Semantic Scholar's similarity capability](../02-discovery.md#semantic-scholars-similarity-capability-split-by-corpus-scope-not-retrieval-shape)
