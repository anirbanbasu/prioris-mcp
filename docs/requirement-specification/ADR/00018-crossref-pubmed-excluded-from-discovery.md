---
status: accepted
date: 2026-08-12
---

# ADR-00018: Crossref and PubMed are excluded from discovery

## Context

[Discovery scope](00015-discovery-openalex-scope-not-multi-aggregator.md) considered whether Crossref and PubMed should join OpenAlex `search.semantic` as discovery sources.

## Decision

Neither is added. Crossref stays in its existing role as a DOI resolver only ([Architecture → Identifier routing](../01-architecture.md#identifier-routing-grouping-level) resolves DOIs via the `doi.org`/Crossref redirect before provider-specific logic runs). PubMed is dropped from consideration entirely.

## Alternatives considered

- **Crossref as a discovery source** — rejected: its own search API is Solr-based field/text matching, not embeddings — it has no analog to `search.semantic`, so there's no discovery capability to add beyond the resolver role it already has.
- **PubMed (NCBI E-utilities) as a discovery source** — rejected: it's keyword + MeSH-term search, also with no embedding component. More decisively, Europe PMC — already an integrated provider — substantially mirrors PubMed/MEDLINE content plus PMC full text and preprints, so a direct PubMed integration would be close to pure redundancy with a provider PriorisMCP already has, for no new capability.

## Referenced from

- [Discovery → Crossref and PubMed stay out of discovery](../02-discovery.md#crossref-and-pubmed-stay-out-of-discovery)
