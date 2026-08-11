---
icon: lucide/globe
---

# Debate: online lookup beyond arXiv/Europe PMC

**Status: to be written.** A placeholder, opened so the topic has a home rather than being lost — not yet discussed in any depth, unlike [100](100-debate-vector-search.md)/[101](101-debate-graph-search.md), which at least reached settled ground or a concrete starting point.

## What this debate is about

v1's `ResearchPublicationProvider` search capability is keyword search scoped to whichever single source (arXiv, Europe PMC) the caller already picked. This debate is about whether/how PriorisMCP should also reach broader scholarly-metadata aggregators — **OpenAlex** and **Crossref** named specifically — rather than being limited to per-source keyword search against sources PriorisMCP already has a dedicated provider for.

A few things worth noting as starting context, not yet resolved into decisions:

- **Crossref already has a foothold here, implicitly.** [Architecture → Identifier routing](01-architecture.md#identifier-routing-grouping-level) already resolves DOIs "via the DOI system (a `doi.org`/Crossref redirect)" before any provider-specific logic runs — that's Crossref-as-resolver, not Crossref-as-searchable-source. Whether this debate extends that existing touchpoint (richer Crossref metadata beyond redirect resolution) or introduces something structurally new (a queryable aggregator alongside, or instead of, per-source providers) is open.
- **How this fits the provider-grouping architecture is open.** Does OpenAlex/Crossref lookup become a new `ResearchPublicationProvider` implementation (like arXiv/Europe PMC), a grouping-level capability sitting above individual providers (like `research_resolve_identifier` or `research_search_fetched` already do), or something else entirely — e.g. a metadata-enrichment step over results a per-source provider already returned?
- **Relation to the future `GraphBackend` ([101](101-debate-graph-search.md)) is a real, unexplored connection.** OpenAlex in particular exposes a large citation graph (works, authors, venues, concepts) — worth asking, whenever 101 gets picked up in earnest, whether an online citation-graph lookup is a *source* for `GraphSearchBackend`'s structural layer rather than a fully separate concern.
- **Relation to future patent search is explicitly unresolved**, per your own framing. `Architecture → Provider groupings` already names `PatentProvider` as a distinct future grouping, on the reasoning that patents need claims/legal-family-status/citation-graph handling that research publications don't. OpenAlex/Crossref are scholarly-publication-specific — whether *any* of this debate's eventual design generalises to patent lookup (e.g. an analogous "richer aggregator vs. single-source keyword search" question for patent sources like Lens.org, USPTO, or EPO OPS), or whether patents need their own, unrelated version of this debate, hasn't been looked at at all.

Nothing above is a decision — it's the shape of the question, captured so the next session doesn't start from nothing.
