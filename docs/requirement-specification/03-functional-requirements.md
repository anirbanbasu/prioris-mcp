---
icon: lucide/wrench
---

# Functional requirements

This page maps the `ResearchPublicationProvider` capabilities defined in [Architecture](01-architecture.md) onto the concrete MCP tools and resources exposed for the two v1 providers, arXiv and Europe PMC, per the [storage](02-storage.md) semantics already established.

This page states **behavioural requirements** — what each tool must accept conceptually, and what guarantees it makes (error semantics, cost tier, persistence side effects) — not literal wire-level parameter names or JSON schemas. Exact input/output schemas belong in the [interface specification](06-interface-specification.md), written once the arXiv and Europe PMC APIs have actually been read during implementation; pinning them down here risks the SRS being wrong in a way implementation would then have to chase.

## Tool surface: per-provider, domain-prefixed

`search`, `fetch_metadata`, `fetch_full_text`, and `parse_full_text` are each exposed as **per-provider** tools, not one generic tool parameterised by provider: `research_arxiv_*` and `research_europepmc_*`. (`list_top_n` follows the same per-provider naming convention but is arXiv-only in v1 — see below.) Two reasons, both about keeping an MCP client's (an LLM's) job easier and its mistakes fewer:

- **Schema tightness.** Identifier patterns (an arXiv ID vs. a Europe PMC identifier) and valid `format` values (which formats a given provider/item actually offers) genuinely differ per provider. A generic tool would need either a loose, unvalidated identifier field, or a `format` enum whose valid values secretly depend on whatever provider value was also passed — neither is expressible cleanly as a JSON schema, and both push validation into runtime code instead of the tool's own contract.
- **Grouping without collision.** The `research_` prefix isn't there to disambiguate — `arxiv_fetch_metadata` is already unambiguous on its own — it's a scanability convention for a flat MCP tool list, so all research-publication tools sort and group together regardless of source, and won't collide with a future `patent_*` domain's tools of the same shape (e.g. `patent_uspto_fetch_metadata`).

`resolve_identifier` is the one capability that does **not** follow this pattern — see below.

## Identifier resolution is a grouping-level tool, not per-provider

Unlike the other five capabilities, `resolve_identifier` is exposed as a **single** MCP tool, `research_resolve_identifier`, not split per provider. This follows directly from [Architecture → Identifier routing](01-architecture.md#identifier-routing-grouping-level): an arXiv ID is self-identifying, but a DOI is not — nothing about a DOI's shape says whether arXiv, Europe PMC, or neither can serve it, so routing has to happen above any single provider, not through one provider's tool guessing at content it doesn't own.

`research_resolve_identifier` must:

- Accept an identifier (an arXiv ID, a Europe PMC identifier, or a DOI) and a desired target format.
- Route self-identifying identifier schemes directly to the owning provider's internal resolution, without a network round-trip to determine ownership.
- Route DOIs through the DOI system (a `doi.org`/Crossref redirect) first, then hand off to whichever v1 provider's domain the redirect landed on, if any.
- Return the resolved URL, the resolved format, the canonical (version-pinned, where applicable) identifier, and which provider will service subsequent calls (`fetch_metadata`, `fetch_full_text`, ...) for that identifier.
- Fail with an **unsupported provider** error if a DOI resolves to a domain outside arXiv/Europe PMC, rather than attempting to scrape the landing page — see Architecture for why partial (metadata-only) support was rejected.

Cost: light — routing is, at most, a DOI redirect plus a provider-native resolution call, neither of which downloads full text.

The per-provider native resolution this delegates to (what would have been `research_arxiv_resolve_identifier` / `research_europepmc_resolve_identifier`) is **not** itself an MCP tool — it's internal, used by `research_resolve_identifier` and by each provider's own `fetch_full_text`.

## Search and listing results carry full metadata

Both arXiv's and Europe PMC's search responses already include full record metadata (title, authors, abstract, identifiers, ...) per hit — neither API returns bare IDs that would need a follow-up call to resolve. `research_arxiv_search`, `research_arxiv_list_top_n`, and `research_europepmc_search` therefore all return the **same metadata shape as `fetch_metadata`**, one entry per result, so a caller doesn't pay a second round-trip just to see what a search already returned. `fetch_metadata` remains useful on its own for the case where a caller already has one specific identifier (e.g. from a citation) and wants its metadata without searching for it.

## arXiv tools

| Tool | Requirement | Cost |
|---|---|---|
| `research_arxiv_search` | Must accept a free-text query and a way to bound the number of results returned; returns metadata records, one per hit. | Light |
| `research_arxiv_list_top_n` | Must accept an arXiv subject category and a count N; returns metadata records for the top N items in that category. | Light |
| `research_arxiv_fetch_metadata` | Must accept **one or more** arXiv identifiers in a single call (arXiv's own API accepts a comma-delimited ID list, making batched lookup a single rate-limited request instead of one per identifier); returns one metadata record per identifier arXiv recognises, plus which of the requested identifiers were not found — it must not fail the whole call just because some requested identifiers don't exist. | Light |
| `research_arxiv_fetch_full_text` | Must accept an arXiv identifier and a format valid for that item (arXiv exposes both PDF and HTML); returns a reference to the persisted content (location, format, size), whether it was served from storage or freshly downloaded, and the resource URI for direct re-reads. | Heavy on a storage miss; light on a storage hit |
| `research_arxiv_parse_full_text` | Must accept an arXiv identifier and the source format to parse, plus optional `offset`/`limit`; returns one bounded page of Markdown (see [Non-functional requirements → Response size](04-non-functional-requirements.md#response-size)) plus the resource URI for direct re-reads; fails with the single "not found" error (see [Architecture → `parse_full_text`](01-architecture.md)) if the source format isn't already persisted — it never triggers a fetch itself. | Heavy (CPU) on a first parse; light if already persisted |

All arXiv tools are subject to arXiv's documented rate limit — max **1 request per 3 seconds** (see [arXiv API terms of use](https://info.arxiv.org/help/api/tou.html), already cited in the [SRS overview](index.md)) — enforced by the arXiv provider itself, not by the response-caching middleware (see [Architecture → Caching and rate limiting](01-architecture.md)). See [Non-functional requirements](04-non-functional-requirements.md) for how this limit is upheld when multiple calls to arXiv tools happen concurrently.

## Europe PMC tools

| Tool | Requirement | Cost |
|---|---|---|
| `research_europepmc_search` | Must accept a free-text query and pagination appropriate to the Europe PMC REST API; returns metadata records, one per hit. | Light |
| `research_europepmc_fetch_metadata` | Must accept **one or more** Europe PMC identifiers in a single call (Europe PMC has no dedicated batch parameter like arXiv's, but its query syntax can `OR` several identifier lookups into one request — a different mechanism achieving the same batching goal); returns one metadata record per identifier Europe PMC recognises, plus which of the requested identifiers were not found. | Light |
| `research_europepmc_fetch_full_text` | Must accept a Europe PMC identifier. Unlike the arXiv equivalent, `format` is **not** a caller-supplied parameter — Europe PMC's only directly-servable full-text format is JATS XML (see [Interface specification](06-interface-specification.md)), so there is no choice to expose. Fails with a not-found error for an unrecognised identifier, or `format_unavailable` if Europe PMC doesn't host full text for that item itself. | Heavy on a storage miss; light on a storage hit |
| `research_europepmc_parse_full_text` | Must accept a Europe PMC identifier (no `format` parameter, for the same reason as above — the persisted source is always XML) plus optional `offset`/`limit`; returns one bounded page of Markdown (see [Non-functional requirements → Response size](04-non-functional-requirements.md#response-size)) plus the resource URI; fails with the single "not found" error if the XML hasn't already been fetched. | Heavy (CPU) on a first parse; light if already persisted |

There is no `research_europepmc_list_top_n` — Europe PMC has no single classification field equivalent to arXiv's subject categories, only several parallel multi-valued tagging schemes (keywords, Gene Ontology terms, disease/organism/method tags); see [SRS overview → Out of scope for v1](index.md#out-of-scope-for-v1) for the full list and why v1 doesn't force a mismatched mapping onto one of them.

Europe PMC's [RESTful Web Service documentation](https://europepmc.org/RestfulWebService) does not publish a specific numeric rate limit. In its absence, **the Europe PMC provider self-imposes the same limit as arXiv — max 1 request per 3 seconds** — as a conservative, externally-justified default rather than an arbitrary invented figure, documented here so it isn't a silent implementation choice. This should be revisited if EBI publishes explicit guidance of its own.

## Resources

Two resource templates expose content that `StorageBackend` already has, as a read-only alternative to re-invoking a tool:

| Resource template | Returns |
|---|---|
| `research://{provider}/{identifier}/{format}/fulltext` | The persisted full text for that item/format, if present. |
| `research://{provider}/{identifier}/{format}/markdown{?offset,limit}` | One paginated page of the persisted parsed Markdown for that item/format, if present — see [Non-functional requirements → Inline text is paginated, not returned whole](04-non-functional-requirements.md#inline-text-is-paginated-not-returned-whole). |

These are read-only and never trigger a fetch or a parse — reading a resource that doesn't exist yet is a normal "not found," not an error the caller needs special handling for beyond "go call the tool first." `research_*_fetch_full_text` and `research_*_parse_full_text` return the corresponding resource URI in their output specifically so a caller can re-read the same content later without re-invoking the tool.

Metadata is **not** exposed as a resource: it's only ever response-cached (TTL-bound via `ResponseCachingMiddleware`), never written to `StorageBackend`, so there's no stable "it's just there" location for it the way there is for full text and Markdown — a metadata resource would be indistinguishable from just calling the tool again.
