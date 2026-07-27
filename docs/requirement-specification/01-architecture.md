---
icon: lucide/layers
---

# Architecture

## Provider groupings

Prior-art sources are grouped by domain, not treated as one flat list of sources. Each grouping gets its own provider interface, because the operations and data shapes that make sense for one domain do not necessarily fit another:

- **`ResearchPublicationProvider`** — research publications (arXiv, Europe PMC in v1). Specified below.
- **`PatentProvider`** _(future, out of scope for v1)_ — patents. Expected to need claims, legal/family status, and citation-graph concerns that research publications don't have.

A source (e.g. arXiv) is one concrete implementation of the provider interface for its grouping. Adding a source means implementing the grouping's interface, not changing the interface itself.

## `ResearchPublicationProvider`

The capability names below (`search`, `fetch_metadata`, ...) name the shared interface every research-publication provider implements — they are not necessarily the public MCP tool names. See [Functional requirements](03-functional-requirements.md) for how these map onto actual tools for arXiv and Europe PMC in v1.

### Capabilities

| Capability | Description | Relative cost |
|---|---|---|
| `search` | Search items by keyword/query. | Light |
| `list_top_n` | List the top-N items for a category, where "category" is a provider-defined grouping (e.g. an arXiv subject class). v1 implements this for arXiv only — see below. | Light |
| `fetch_metadata` | Fetch metadata (title, authors, abstract, identifiers, category, dates, links, ...) for one or more items in a single call — batching is worthwhile precisely because rate limiting (see below) makes N separate single-item calls costlier than one call for N identifiers. | Light |
| `resolve_identifier` | Given an identifier already known to belong to this provider, resolve it to a fetchable URL in the target format (e.g. HTML, PDF), pinning a canonical version where the provider's identifiers are mutable. This is an internal, provider-native capability — see below. | Light |
| `fetch_full_text` | Fetch the full text of a single item, in a given format, from its resolved URL. | Heavy (network I/O) |
| `parse_full_text` | Convert previously-fetched full text into Markdown. | Heavy (CPU-bound) |

`fetch_metadata` and `fetch_full_text` are deliberately separate operations, not two modes of one call. Metadata is small and cheap; full text means downloading and persisting a document (through the [storage abstraction](02-storage.md)), which is a materially heavier operation. Keeping them separate lets a client (or the MCP tool surface) request metadata without paying the cost of a full-text fetch it doesn't need.

Not every provider necessarily implements every capability at full strength — a provider that can't sensibly support a capability should say so rather than fake it. `list_top_n` is the concrete v1 example: it's provider-defined ("category" means an arXiv subject class), and Europe PMC has no equivalent single classification field, only several parallel multi-valued tagging schemes (see [Functional requirements → Europe PMC tools](03-functional-requirements.md#europe-pmc-tools)), so v1 simply doesn't implement `list_top_n` for Europe PMC rather than force a mismatched mapping.

### `resolve_identifier`

Converts an identifier a caller already has (DOI, arXiv ID, ...) into a URL that can actually be fetched over HTTP, given a requested target format (e.g. HTML, PDF). This is what `fetch_full_text` uses internally to know what to download — separating "which URL" from "go get it" keeps identifier/format resolution logic (which varies per provider and can change independently, e.g. if a source changes its URL scheme) out of the download path itself.

For providers whose identifiers don't have a fixed meaning over time — arXiv's unversioned IDs mean "whatever is currently latest" — `resolve_identifier` is also responsible for resolving to the current concrete, canonical (version-pinned) identifier, not just a URL. That canonical identifier, not the caller's original one, is what `fetch_full_text` and the [storage layer](02-storage.md#identifier-canonicalisation) use from that point on; see storage's identifier canonicalisation section for why this matters.

As described above, this is a **provider-native** capability: it assumes the caller already knows which provider owns the identifier (an arXiv ID is only ever an arXiv ID). It is not itself exposed as an MCP tool — it is invoked internally, by `fetch_full_text` and by the grouping-level routing below.

### Identifier routing (grouping-level)

Not every identifier a caller has is provider-native. An arXiv ID is self-identifying — its shape alone says which provider owns it, no lookup required. A DOI is not: its prefix doesn't reveal whether the item is on arXiv, indexed by Europe PMC, or published somewhere PriorisMCP has no provider for at all. Routing an arbitrary identifier to the right provider is therefore a capability of the `ResearchPublicationProvider` *grouping*, sitting above any single provider, not something one provider can answer on another's behalf.

This is exposed as a single MCP tool, `research_resolve_identifier` (see [Functional requirements](03-functional-requirements.md)), which routes as follows:

- **Self-identifying schemes** (an arXiv ID, or Europe PMC's own identifier schemes) route directly to that provider's native `resolve_identifier` — no network round-trip is needed to know who owns them.
- **DOIs** always resolve via the DOI system (a `doi.org`/Crossref redirect) first, before any provider-specific logic runs. If the resulting landing domain belongs to a v1 provider (arxiv.org, europepmc.org/NCBI PMC), routing hands off to that provider's native `resolve_identifier` from there.
- If a DOI resolves to a domain that isn't a supported provider (e.g. a publisher's own site), routing fails with an **unsupported provider** error rather than attempting to scrape the landing page. Scraping arbitrary publisher HTML is a materially different, unvetted capability — it wasn't checked against that publisher's terms of use the way arXiv's and Europe PMC's documented APIs were, and a flat error is a predictable outcome instead of a partial, silently-degraded one (metadata scraped, full text unreachable behind a paywall).

### `parse_full_text`

Converts full text already retrieved by `fetch_full_text` into Markdown. It is a distinct, third operation alongside `fetch_full_text` — not a mode or flag on it — for the same reason `fetch_metadata` and `fetch_full_text` are kept apart: `fetch_full_text` is network-bound, `parse_full_text` is CPU-bound, and collapsing them would mean every parse implicitly pays for (and depends on the availability of) a network fetch.

`parse_full_text` operates only on content the storage abstraction already has; it must not silently trigger a `fetch_full_text` call. If the requested item/format has not been fetched yet, or was fetched but is no longer present in storage, `parse_full_text` fails with a single "not found" error that names the missing item and format, telling the caller to `fetch_full_text` it first — v1 does not distinguish "never fetched" from "fetched then evicted" as separate error cases. This keeps each capability's cost and side effects predictable and explicit rather than having a "just parse this" call unexpectedly perform a network fetch — `parse_full_text` is never a superset of `fetch_full_text`. It also gives the calling LLM a decision point: on seeing the error, it can call `fetch_full_text` itself or defer to the user for consent before doing so, rather than a fetch happening as an invisible side effect of what looked like a parse request.

Converting full text to Markdown is implemented via a pluggable parser-backend interface, one per source format (PDF, HTML, ...), mirroring [`StorageBackend`](02-storage.md#storagebackend)'s interface-plus-swappable-implementation shape rather than being hardcoded to one library per format. v1 ships exactly one backend per format — a structure-aware backend for arXiv's PDF full text, and an HTML-to-Markdown backend for arXiv's HTML full text (reused, via an XSLT transform to HTML first, for Europe PMC's JATS XML) — chosen for the specific input each format actually presents in v1, not for hypothetical future inputs. A different backend for a format (e.g. a slower but more sophisticated general-purpose document parser for PDF, or a boilerplate-removal-oriented converter for messier HTML once a future non-arXiv source needs it) can be swapped in later by adding an implementation of the interface and selecting it via configuration, without changing the interface itself — the same pattern [Storage](02-storage.md#future-s3-or-other-remoteobject-backend) already uses for its own future S3 backend. See [Non-functional requirements → Dependency selection](04-non-functional-requirements.md#dependency-selection) for the criteria v1's backend choices are held to.

### Content model

`fetch_full_text` returns a typed content result, not a bare blob — at minimum a **format** (e.g. PDF, HTML; ePub or others as providers require) alongside the content/location. arXiv, for instance, exposes both PDF and HTML full text for the same item, so the interface treats format as a first-class, provider/item-dependent property rather than assuming one fixed format across all sources.

Metadata results are similarly typed, but as structured fields (title, authors, abstract, identifiers, etc.) rather than a format-tagged document.

`resolve_identifier` returns a URL alongside the format it resolves to (since the same identifier can resolve to different URLs for different formats); the grouping-level `research_resolve_identifier` tool additionally returns which provider will service subsequent calls for that identifier. `parse_full_text`'s result is always Markdown — it does not carry a format tag, since converting to Markdown is the point of the operation.

### Caching and rate limiting

Response caching is **not** a provider concern. The server already applies `ResponseCachingMiddleware` (see `server.py`) to tool/resource calls; `search`, `list_top_n`, and `fetch_metadata` results are cached there like any other tool response. Providers should not implement their own duplicate caching layer.

Rate limiting **is** a provider concern, and a distinct one from caching: it exists to satisfy each source's terms of use (e.g. arXiv's API rate limits), not to avoid redundant work, and applies per outbound request to the source regardless of whether the response ends up cached. See [Non-functional requirements](04-non-functional-requirements.md) for how rate limiting behaves under concurrent tool invocations.
