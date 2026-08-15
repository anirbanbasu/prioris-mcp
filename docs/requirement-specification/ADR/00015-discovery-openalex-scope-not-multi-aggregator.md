---
status: accepted
date: 2026-08-12
---

# ADR-00015: Discovery scope is OpenAlex `search.semantic`, not a general multi-aggregator expansion

## Context

[`ResearchPublicationProvider`](../01-architecture.md)'s `search` capability is keyword search scoped to a single source (arXiv, Europe PMC). [OpenAlex's `search.semantic`](https://developers.openalex.org/guides/semantic-search) offers something genuinely different: free-text input up to 2,000 characters, embedded and ranked by cosine similarity against every indexed work's own title/abstract embedding — real embedding-based retrieval, not keyword matching. The question is whether "discovery" should mean this one capability, or a general pattern extended across other aggregators (Semantic Scholar, PubMed, CORE) that also index scholarly works.

## Decision

Discovery scope is narrowly OpenAlex's `search.semantic` parameter on `/works`.

## Alternatives considered

- **Fanning discovery out across Semantic Scholar, PubMed, and CORE as peer discovery sources** — rejected as a false generalisation: none of the three actually replicate `search.semantic`'s embedding-based retrieval (see [ADR-00018: Crossref and PubMed excluded from discovery](00018-crossref-pubmed-excluded-from-discovery.md) and [ADR-00019: Semantic Scholar's similarity capability — placement, not adoption](00019-semantic-scholar-placement-not-adoption.md) for the per-source reasoning). "Extend discovery to more aggregators" turned out not to be one decision applied three times, but three unrelated questions that only look similar from a distance.

## Consequences

Practical limits inherited from the API itself apply directly: maximum 50 results per query, rate-limited to 1 request/second, and it can't be combined with OpenAlex's other `search`/`search.exact` parameters in the same request. Input over 2,000 characters is a separate, PriorisMCP-imposed policy, not an inherited OpenAlex behaviour: OpenAlex's own `search.semantic` silently uses just the first 2,000 characters of a longer query rather than rejecting it, but `research_discovery` rejects it outright with `invalid_request` instead of silently discarding part of the caller's input — see [Interface specification → `research_discovery`](../06-interface-specification.md#research_discovery).

## Referenced from

- [Discovery → Scope](../02-discovery.md#scope-openalex-searchsemantic-not-a-general-multi-aggregator-expansion)
