---
icon: lucide/list-checks
---

# Software Requirements Specification

This section is the Software Requirements Specification (SRS) for PriorisMCP. It is both user-readable documentation and the working design reference for implementation — requirements land here before code, and this section is expected to evolve alongside the server as new source providers and capabilities are added.

## Purpose

PriorisMCP is a [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that gives an MCP client (e.g. Claude) a single, uniform way to browse, search, fetch, and parse prior art from external sources — starting with research publications, with patents and other prior-art domains anticipated as future work.

## Scope

### v1

- One provider domain: **research publications**.
- Two providers within that domain:
    - **arXiv** — open-access, no authentication required.
    - **Europe PMC** — open-access, no authentication required, biomedical/life-sciences literature.
- Capabilities exposed per provider (exact availability is provider-dependent — not every provider necessarily supports every capability):
    - **Search** by keyword/query.
    - **List** top-N items by category (e.g. arXiv subject classification; Europe PMC's equivalent grouping).
    - **Fetch metadata** for a given item — a distinct, lightweight operation, separate from full-text retrieval.
    - **Fetch full text** for a given item — a separate, heavier operation than metadata fetch; the returned format depends on what the provider/item offers (e.g. arXiv exposes both PDF and HTML full text; other providers may differ).
    - **Parse full text** — convert previously-fetched full text into Markdown, operating on already-persisted content rather than re-fetching it.
- **Resolve identifier** — a grouping-level capability, not per-provider: convert an item identifier (e.g. DOI, arXiv ID) into a fetchable URL, parameterised by the desired target format (e.g. HTML, PDF). Self-identifying identifier schemes (an arXiv ID) route directly to their provider; DOIs resolve via the DOI system first, then route to whichever v1 provider (if any) the redirect lands on.
- Fetched full text is persisted through a storage abstraction, with a local filesystem backend as the default.
- Repeated identical requests are served through the server's existing response-caching middleware rather than a separate, provider-specific caching layer.

Two open-access, unauthenticated providers are deliberately chosen for v1 so that the provider interface (see [Architecture](01-architecture.md)) is validated against two genuinely different APIs and content shapes, rather than being modelled after a single source and assumed to generalise.

### Out of scope for v1

- **Patents.** Anticipated as a separate provider grouping (`PatentProvider`) once the research-publication providers have exercised the shared abstractions. Patent records differ enough (claims, legal/family status, citation graphs) that they are not expected to fit the same interface as research publications without changes.
- **Authenticated sources** (e.g. Semantic Scholar, or any source requiring an API key or OAuth). v1 targets unauthenticated, open-access sources only; authenticated sources are future work once credential handling and per-source rate/quota policies are designed.
- **S3 (or other remote/object) storage backend.** The storage abstraction is designed for it from the start (see [Storage](02-storage.md)), but v1 implements the local filesystem backend only.

## Assumptions and dependencies

- **Provider availability.** PriorisMCP depends on arXiv's and Europe PMC's APIs remaining available and stable in shape; an outage or breaking API change in either is an external dependency, not something this SRS specifies behaviour for beyond the error semantics already described per-capability (see [Functional requirements](03-functional-requirements.md)).
- **Consent for heavier operations rests with the MCP client.** PriorisMCP assumes the calling MCP client (the LLM, or the human behind it) is the point where consent for a heavier operation is decided — e.g. `parse_full_text` failing explicitly rather than silently triggering `fetch_full_text` (see [Architecture → `parse_full_text`](01-architecture.md#parse_full_text)) exists specifically so that decision point remains with the caller, not PriorisMCP.
- **Content licensing is separate from rate-limit terms of use.** The rate limits documented in [References](#references) govern *how often* PriorisMCP may call arXiv/Europe PMC; they say nothing about the licensing terms attached to the content itself once fetched. arXiv and Europe PMC articles carry their own (often per-article) licences — some permissive, some not — governing redistribution of full text. v1 persists fetched full text indefinitely via the [storage abstraction](02-storage.md) as a caching/de-duplication mechanism for the same MCP client that already fetched it; this SRS does not currently address longer-term redistribution of persisted content beyond that use, and this should be revisited before any feature that shares persisted content beyond the fetching client is considered.

## Definitions and acronyms

| Term | Meaning |
|---|---|
| MCP | Model Context Protocol |
| SRS | Software Requirements Specification (this document) |
| Provider | A component implementing the interface for one grouping of prior-art sources (research publications, patents, ...) |
| Source | A single external system a provider talks to (e.g. arXiv, Europe PMC) |

## References

- [arXiv API user manual](https://info.arxiv.org/help/api/index.html) and its [terms of use](https://info.arxiv.org/help/api/tou.html) (rate limits apply).
- [Europe PMC RESTful Web Service](https://europepmc.org/RestfulWebService).

## Document structure

- [Architecture](01-architecture.md) — provider groupings and the `ResearchPublicationProvider` interface.
- [Storage](02-storage.md) — the `StorageBackend` abstraction and its local-filesystem and (future) S3 implementations.
- [Functional requirements](03-functional-requirements.md) — the concrete tools/resources exposed for arXiv and Europe PMC in v1, in behavioural terms.
- [Non-functional requirements](04-non-functional-requirements.md) — cross-cutting qualities, currently concurrency.
- [Security](05-security.md) — untrusted-identifier and untrusted-content requirements.
- [Interface specification](06-interface-specification.md) — exact MCP wire-level input/output schemas (placeholder, deferred until the provider APIs are read).
- [Test specification](07-test-specification.md) — verification/acceptance criteria per capability (placeholder, deferred until the interface specification exists).
