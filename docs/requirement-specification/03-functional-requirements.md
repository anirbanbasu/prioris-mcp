---
icon: lucide/wrench
---

# Functional requirements

This page maps the `ResearchPublicationProvider` capabilities defined in [Architecture](01-architecture.md) onto the concrete MCP tools and resources exposed for the two v1 providers, arXiv and Europe PMC, per the [storage](02-storage.md) semantics already established.

This page states **behavioural requirements** — what each tool must accept conceptually, and what guarantees it makes (error semantics, cost tier, persistence side effects) — not literal wire-level parameter names or JSON schemas. Exact input/output schemas belong in the [interface specification](06-interface-specification.md), written once the arXiv and Europe PMC APIs have actually been read during implementation; pinning them down here risks the SRS being wrong in a way implementation would then have to chase.

## Tool surface: per-provider, domain-prefixed

`search`, `list_top_n`, `fetch_metadata`, `fetch_full_text`, and `parse_full_text` are each exposed as **per-provider** tools, not one generic tool parameterised by provider: `research_arxiv_*` and `research_europepmc_*`. Two reasons, both about keeping an MCP client's (an LLM's) job easier and its mistakes fewer:

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

Both arXiv's and Europe PMC's search/listing responses already include full record metadata (title, authors, abstract, identifiers, ...) per hit — neither API returns bare IDs that would need a follow-up call to resolve. `research_arxiv_search`, `research_arxiv_list_top_n`, `research_europepmc_search`, and `research_europepmc_list_top_n` therefore all return the **same metadata shape as `fetch_metadata`**, one entry per result, so a caller doesn't pay a second round-trip just to see what a search already returned. `fetch_metadata` remains useful on its own for the case where a caller already has one specific identifier (e.g. from a citation) and wants its metadata without searching for it.

## arXiv tools

| Tool | Requirement | Cost |
|---|---|---|
| `research_arxiv_search` | Must accept a free-text query and a way to bound the number of results returned; returns metadata records, one per hit. | Light |
| `research_arxiv_list_top_n` | Must accept an arXiv subject category and a count N; returns metadata records for the top N items in that category. | Light |
| `research_arxiv_fetch_metadata` | Must accept a single arXiv identifier; returns one metadata record; must fail with a not-found error for an identifier arXiv doesn't recognise. | Light |
| `research_arxiv_fetch_full_text` | Must accept an arXiv identifier and a format valid for that item (arXiv exposes both PDF and HTML); returns a reference to the persisted content (location, format, size), whether it was served from storage or freshly downloaded, and the resource URI for direct re-reads. | Heavy on a storage miss; light on a storage hit |
| `research_arxiv_parse_full_text` | Must accept an arXiv identifier and the source format to parse; returns Markdown plus the resource URI for direct re-reads; fails with the single "not found" error (see [Architecture → `parse_full_text`](01-architecture.md)) if the source format isn't already persisted — it never triggers a fetch itself. | Heavy (CPU) on a first parse; light if already persisted |

All arXiv tools are subject to arXiv's documented rate limit — max **1 request per 3 seconds** (see [arXiv API terms of use](https://info.arxiv.org/help/api/tou.html), already cited in the [SRS overview](index.md)) — enforced by the arXiv provider itself, not by the response-caching middleware (see [Architecture → Caching and rate limiting](01-architecture.md)). See [Non-functional requirements](04-non-functional-requirements.md) for how this limit is upheld when multiple calls to arXiv tools happen concurrently.

## Europe PMC tools

| Tool | Requirement | Cost |
|---|---|---|
| `research_europepmc_search` | Must accept a free-text query and pagination appropriate to the Europe PMC REST API; returns metadata records, one per hit. | Light |
| `research_europepmc_list_top_n` | Must accept a category (Europe PMC's equivalent grouping — exact taxonomy to pin down at implementation time, since Europe PMC doesn't have a single fixed classification scheme equivalent to arXiv's) and a count N; returns metadata records. | Light |
| `research_europepmc_fetch_metadata` | Must accept a single Europe PMC identifier; returns one metadata record; must fail with a not-found error for an identifier Europe PMC doesn't recognise. | Light |
| `research_europepmc_fetch_full_text` | Same requirement shape as the arXiv equivalent. | Heavy on a storage miss; light on a storage hit |
| `research_europepmc_parse_full_text` | Same requirement shape as the arXiv equivalent. | Heavy (CPU) on a first parse; light if already persisted |

Europe PMC's [RESTful Web Service documentation](https://europepmc.org/RestfulWebService) does not publish a specific numeric rate limit. In its absence, **the Europe PMC provider self-imposes the same limit as arXiv — max 1 request per 3 seconds** — as a conservative, externally-justified default rather than an arbitrary invented figure, documented here so it isn't a silent implementation choice. This should be revisited if EBI publishes explicit guidance of its own.

## Resources

Two resource templates expose content that `StorageBackend` already has, as a read-only alternative to re-invoking a tool:

| Resource template | Returns |
|---|---|
| `research://{provider}/{identifier}/{format}/fulltext` | The persisted full text for that item/format, if present. |
| `research://{provider}/{identifier}/{format}/markdown` | The persisted parsed Markdown for that item/format, if present. |

These are read-only and never trigger a fetch or a parse — reading a resource that doesn't exist yet is a normal "not found," not an error the caller needs special handling for beyond "go call the tool first." `research_*_fetch_full_text` and `research_*_parse_full_text` return the corresponding resource URI in their output specifically so a caller can re-read the same content later without re-invoking the tool.

Metadata is **not** exposed as a resource: it's only ever response-cached (TTL-bound via `ResponseCachingMiddleware`), never written to `StorageBackend`, so there's no stable "it's just there" location for it the way there is for full text and Markdown — a metadata resource would be indistinguishable from just calling the tool again.

## Next

- [Non-functional requirements](04-non-functional-requirements.md) — concurrency and other cross-cutting qualities not tied to a single tool.
- [Security](05-security.md) — untrusted-identifier and untrusted-content requirements that apply to `research_resolve_identifier` and `parse_full_text` above.
- [Interface specification](06-interface-specification.md) and [test specification](07-test-specification.md) exist as pages but are deliberately deferred (see each page) until the provider APIs are better understood.
