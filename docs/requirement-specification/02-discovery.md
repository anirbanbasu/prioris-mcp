---
icon: lucide/globe
---

# Discovery

**v3** — see [SRS overview → Scope](index.md#v3). `ResearchPublicationProvider`'s `search` capability (arXiv, Europe PMC) is keyword search scoped to whichever single source the caller already picked — a query has to share vocabulary with a paper's title/abstract to find it, and results are limited to whichever one source was queried. Discovery adds an embedding-based, cross-source-ranked alternative on top of that, specifically via [OpenAlex's `search.semantic`](https://developers.openalex.org/guides/semantic-search): a dedicated endpoint that accepts long free-text input (up to 2,000 characters — an abstract, a grant summary, a paragraph of notes) and returns works ranked by embedding similarity rather than keyword overlap.

**This is a different "semantic" from [Vector search](search/02-vector-search.md)'s.** `VectorSearchBackend` is PriorisMCP's own local embedding index over documents/notes *already fetched* into this project's storage. Discovery is a remote, third-party mechanism over an external catalogue of works *not yet* in the local corpus at all. They are unrelated at the mechanism level (OpenAlex's GTE Large EN embeddings vs. this project's configurable `EmbeddingBackend`/`fastembed` default) and unrelated at the data-scope level (external candidate discovery vs. local retrieval over content the user already has). The only thing they share is the word "semantic" — worth naming explicitly so a reader of this SRS doesn't conflate the two.

## Scope: OpenAlex `search.semantic`, not a general multi-aggregator expansion

The capability this chapter designs is narrowly OpenAlex's `search.semantic` parameter on `/works`: it embeds the title and abstract of every indexed work using GTE Large EN (1,024-dimensional), embeds the query the same way at search time, and ranks by cosine similarity — genuine embedding-based retrieval, not Elasticsearch relevance scoring dressed up as "semantic." See [ADR-00015: Discovery scope is OpenAlex `search.semantic`, not a general multi-aggregator expansion](ADR/00015-discovery-openalex-scope-not-multi-aggregator.md) for why this doesn't extend to Semantic Scholar, PubMed, or CORE, and for the API's practical limits.

## Authentication: an API key, not `mailto`

OpenAlex deprecated the `mailto` polite-pool query parameter in favour of a free API key, then went further: since 2026-02-13, an API key is **required** on every request — an unauthenticated call now fails outright rather than merely losing the higher rate limits a key grants (see [OpenAlex → Authentication](https://help.openalex.org/api/authentication)). `PRIORIS_MCP_OPENALEX_API_KEY` configures it, sent as the `api_key` query parameter on every outbound request `OpenAlexClient` makes. The environment variable itself stays optional at server startup (`default=None`) so an unconfigured key doesn't break unrelated tools; `OpenAlexClient.search_semantic`/`list_work_types` instead raise `ConfigurationError` (`configuration_error`) with an actionable message the moment `research_discovery`/`research://openalex/work-types` is actually used without one — see [Interface specification → Conventions](06-interface-specification.md#conventions).

## Discovery-only, not a `ResearchPublicationProvider` peer

A `search.semantic` hit is metadata (title, abstract, authors, OA-location pointer if one exists) — the endpoint itself returns ranked candidates, not full text. OpenAlex does now host first-party full text for a large share of its corpus (PDFs/TEI XML, filterable via `has_content.pdf`), a materially different, sanctioned mechanism from the `best_oa_location` external pointer the "Auto-fetch rejected" section below addresses. Whether that hosting is enough to make OpenAlex a full `ResearchPublicationProvider` is genuinely undesigned and deliberately out of scope here — tracked as its own future debate ([#33](https://github.com/anirbanbasu/prioris-mcp/issues/33)), not decided in this chapter.

Within this chapter's scope, OpenAlex stays discovery-only, sitting in front of the providers/mechanisms that actually produce full text.

## Surfacing shape: a new tool, `research_discovery`, not a `mode` on `research_search_fetched`

[Vector search](search/02-vector-search.md) settles `research_search_fetched` exposing `fts`/`vector`/`hybrid` via a single `mode` parameter — one tool, several retrieval mechanisms, all sharing one domain: local corpus content already fetched into storage, returning a consistent chunk/doc-shaped result.

Discovery gets its own tool instead, `research_discovery`, mirroring `research_arxiv_search`/`research_europepmc_search` in naming — but unlike those two, it isn't provider-backed. Discovery is a capability layer sitting in front of providers, not a provider itself, so `research_discovery` has no `ResearchPublicationProvider` behind it unless/until [#33](https://github.com/anirbanbasu/prioris-mcp/issues/33) changes that. See [ADR-00016: `research_discovery` is a new tool, not a `mode` on `research_search_fetched`](ADR/00016-research-discovery-new-tool-not-mode.md) for why.

## Paging, filters, and the work-type reference resource

`research_discovery` mirrors OpenAlex's own `page`/`per-page` request shape for pagination rather than this codebase's usual offset/limit convention — `search.semantic` has a hard 50-match ceiling per query, which makes offset-based paging over an unbounded total misleading here. `total`/`has_more` in the response reflect OpenAlex's own pre-filter counts for the query, not the count after local-corpus exclusion (see [Fetch ladder](#fetch-ladder-for-results-that-land-outside-arxiveurope-pmc) below) — a page can come back with fewer hits than requested even though more remain to page through.

Only two of OpenAlex's `/works` filters are exposed, as `from_year`/`to_year` (`publication_year`) and `open_access_only` (`is_oa`): live testing against the real API found these two reliably narrow `search.semantic` results, while `from_publication_date`/`to_publication_date` are rejected outright (not in `search.semantic`'s own supported-filter list) and `type` silently lets a meaningful fraction of non-matching results through rather than actually filtering — an OpenAlex-side limitation, not a client bug, so `type` isn't offered as a filter.

The `research://openalex/work-types` resource (mirroring `research://arxiv/categories`'s pattern) exposes OpenAlex's work-type vocabulary — see [OpenAlex → Work types](https://help.openalex.org/data/work-types) — as reference data for interpreting a hit's own metadata, not as a discovery-tool filter parameter.

## Fetch ladder for results that land outside arXiv/Europe PMC

A `search.semantic` hit that isn't already backed by an arXiv/Europe PMC identifier is handled in order:

1. **Known-provider route.** If the hit resolves to an identifier PriorisMCP already has a provider for (an arXiv ID, a PMC ID), route straight to that provider — no user round-trip.
2. **Surface, don't auto-fetch, an OA link.** If neither provider applies, look beyond `best_oa_location` — OpenAlex's own single "best" pick, which can be null or lower-fidelity even when other OA locations exist — and loop the work's full OA-locations list instead, preferring published > accepted > submitted version, surfacing the first entry with a populated `pdf_url` to the caller as a convenience. PriorisMCP does not fetch it server-side, and validating that `pdf_url` stays to a presence/shape check, not a live HEAD/MIME-type request — see [ADR-00017: Auto-fetch of OA PDF URLs is rejected, for now](ADR/00017-auto-fetch-oa-pdf-rejected.md) for why, including the live-validation question.
3. **Manual upload fallback.** Otherwise, prompt the user to source the PDF themselves and upload it via the existing local-file ingestion path (already built, OCR included) — no new ingestion mechanism needed for this case.

## Auto-fetch of OA PDF URLs: rejected for now

PriorisMCP does not fetch OA PDF URLs server-side — a hit with no known-provider route and no usable OA link falls through to the manual upload fallback (rung 3, above) instead. See [ADR-00017: Auto-fetch of OA PDF URLs is rejected, for now](ADR/00017-auto-fetch-oa-pdf-rejected.md) for why, and for how this leaves general external-URL fetching as its own future debate rather than something folded into this chapter.

## Crossref and PubMed stay out of discovery

**Crossref** already has a foothold in this architecture as a DOI resolver ([Architecture → Identifier routing](01-architecture.md#identifier-routing-grouping-level) resolves DOIs via the `doi.org`/Crossref redirect before provider-specific logic runs) and that's where it stays. **PubMed** (NCBI E-utilities) is dropped from consideration entirely. See [ADR-00018: Crossref and PubMed are excluded from discovery](ADR/00018-crossref-pubmed-excluded-from-discovery.md) for why.

## Semantic Scholar's similarity capability: split by corpus scope, not retrieval shape

Semantic Scholar's `/paper/search` (and bulk search) is keyword search against an Elasticsearch index with a custom relevance re-ranker — not free-text embedding search over arbitrary input, so it isn't a `search.semantic` peer. It does compute SPECTER2 embeddings per paper, but the only retrieval mode built on them is the Recommendations API, which takes a **seed paper ID** (or a small set of them) and returns similar papers — a citation/embedding-graph-flavored mechanism that raises a corpus-scope placement question: Discovery, or a locally-scoped citation/concept-linking mechanism instead. See [ADR-00019: Semantic Scholar's similarity capability — placement, not adoption](ADR/00019-semantic-scholar-placement-not-adoption.md) for why it would belong alongside `research_discovery` instead, if ever adopted.

## CORE flagged as a future fetch-ladder candidate, not adopted now

CORE's `/search/works`/`/search/fulltext` endpoints are also keyword/relevance search, not embedding-based — so, like Crossref and PubMed, it isn't a `search.semantic` peer either. Its actual differentiator is scale of full-text aggregation: it harvests 10,000+ repositories (including arXiv, Crossref, and PubMed sources) into 300M+ metadata records and 40M+ full-text papers, making it the largest full-text OA aggregator by a wide margin. That's orthogonal to discovery — it's a candidate to strengthen fetch-ladder rung 2 (it may locate open full text that OpenAlex's own OA-location data misses via DOI cross-resolution), not a discovery mechanism to run in place of or alongside `search.semantic`.

Whether/when it's actually adopted isn't decided here: published accuracy figures for OpenAlex's own OA-location coverage are based on small sample sizes, too thin to justify committing to a specific adoption trigger now, and the size of the gap CORE would actually close is entangled with how far the `oa_locations`-loop strengthening above already reduces missed links. Tracked as part of [#34](https://github.com/anirbanbasu/prioris-mcp/issues/34), alongside the live-link-validation question — not a standalone open item in this chapter.

## Patent search is out of scope, deliberately, not by oversight

[Architecture → Provider groupings](01-architecture.md#provider-groupings) already names `PatentProvider` as a distinct future grouping, on the reasoning that patents need claims/legal-family-status/citation-graph handling research publications don't. Whether an analogous "richer discovery vs. single-source keyword search" question applies to patent sources (Lens.org, USPTO, EPO OPS) hasn't been designed here: patents sit in a different rights/licensing landscape than research-publication OA content, and none of this chapter's reasoning (OA-location pointers, arXiv/Europe PMC provider fetch, the SSRF-shaped auto-fetch rejection) obviously carries over to it. This gets its own future debate document when `PatentProvider` is picked up, not a transferred answer from here — noted explicitly so a reader of this chapter doesn't mistake the silence for an oversight.
