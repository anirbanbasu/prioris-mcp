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
    - **List** top-N items by category (arXiv subject classification only — see [Out of scope for v1](#out-of-scope-for-v1) for why Europe PMC doesn't get this capability in v1).
    - **Fetch metadata** for a given item — a distinct, lightweight operation, separate from full-text retrieval.
    - **Fetch full text** for a given item — a separate, heavier operation than metadata fetch; the returned format depends on what the provider/item offers (e.g. arXiv exposes both PDF and HTML full text; other providers may differ).
    - **Parse full text** — convert previously-fetched full text into Markdown, operating on already-persisted content rather than re-fetching it.
- **Resolve identifier** — a grouping-level capability, not per-provider: convert an item identifier (e.g. DOI, arXiv ID) into a fetchable URL, parameterised by the desired target format (e.g. HTML, PDF). Self-identifying identifier schemes (an arXiv ID) route directly to their provider; DOIs resolve via the DOI system first, then route to whichever v1 provider (if any) the redirect lands on.
- Fetched full text is persisted through a storage abstraction, with a local filesystem backend as the default.
- Repeated identical requests are served through the server's existing response-caching middleware rather than a separate, provider-specific caching layer.
- Served over both `stdio` and `streamable-http`/`http` MCP transports (`PRIORIS_MCP_TRANSPORT`, see `src/prioris_mcp/__init__.py`); an agent invoking PriorisMCP via `stdio` is the primary expected path, but v1 doesn't restrict to it. The HTTP transport's ingress security posture is covered in [Security](05-security.md).

Two open-access, unauthenticated providers are deliberately chosen for v1 so that the provider interface (see [Architecture](01-architecture.md)) is validated against two genuinely different APIs and content shapes, rather than being modelled after a single source and assumed to generalise.

### Out of scope for v1

- **Patents.** Anticipated as a separate provider grouping (`PatentProvider`) once the research-publication providers have exercised the shared abstractions. Patent records differ enough (claims, legal/family status, citation graphs) that they are not expected to fit the same interface as research publications without changes.
- **Authenticated sources** (e.g. Semantic Scholar, or any source requiring an API key or OAuth). v1 targets unauthenticated, open-access sources only; authenticated sources are future work once credential handling and per-source rate/quota policies are designed.
- **S3 (or other remote/object) storage backend.** The storage abstraction is designed for it from the start (see [Storage](02-storage.md)), but v1 implements the local filesystem backend only.
- **arXiv's OAI-PMH interface** (`oaipmh.arxiv.org/oai`), a separate bulk/incremental metadata-harvesting protocol distinct from the export API v1 uses. It is not tied to any v1 capability — its intended purpose is "copying and synchronization of a complete set of arXiv metadata," not per-item lookup — but its `arXiv` metadata format does expose per-article license information the export API does not, which may make it useful later (e.g. to enrich `fetch_metadata`). Deferred until that use case is settled, not because of any technical blocker.
- **Europe PMC's Annotations API** (`www.ebi.ac.uk/europepmc/annotations_api`), which provides text-mined entity annotations per article (genes, chemicals, diseases, and similar, tagged to external ontologies). This would be a genuinely new, Europe-PMC-only capability with no arXiv equivalent, not a variant of anything in [Architecture](01-architecture.md#capabilities). Deferred until its licensing/attribution terms for annotation data (separate from the underlying article's own licensing) are confirmed, and until there's a concrete use case for PriorisMCP.
- **`list_top_n` for Europe PMC.** Unlike arXiv, Europe PMC has no single subject-classification field to list top items by — instead it exposes several parallel, multi-valued tagging/labelling schemes: `KEYWORD`/`KW` (free-text keywords), `GOTERM`/`GOTERM_ID` (Gene Ontology terms), `DISEASE`/`DISEASE_ID`, `ORGANISM`/`ORGANISM_ID`, `EXPERIMENTAL_METHOD`/`EXPERIMENTAL_METHOD_ID`, and `PUB_TYPE` (publication type). None of these is a like-for-like match for arXiv's single primary category, so v1 does not implement `list_top_n` for Europe PMC at all rather than force a mismatched mapping onto one of these fields. Revisit if a concrete use case emerges for listing by one of these tag axes (e.g. "top N by `DISEASE`").
- **Storage retention/eviction.** v1 persists fetched full text and parsed Markdown indefinitely (see [Storage](02-storage.md)); there is no size- or time-based eviction. A size-based cap with LRU eviction (evicting least-recently-*read* entries once a configured disk quota is hit) is the intended future direction — see [Storage → Future](02-storage.md#future-retention-and-redistribution-aware-persistence) — since storage keys are content-addressed and immutable, so the problem is disk growth, not staleness. Deferred until there's a concrete need, not because of a technical blocker.
- **Redistribution-policy-aware persistence.** Distinct from retention above: whether persisted content may ever be shared beyond the MCP client that originally fetched it depends on each article's own licence, which v1 does not currently have full visibility into — Europe PMC's metadata already exposes a `license` field, but arXiv's Export API (what v1 uses) exposes no per-article licence at all; only the already-deferred OAI-PMH `arXiv` metadata format does (see above). Aligning persistence/redistribution behaviour with each source's stated terms is future work that depends on that OAI-PMH decision for arXiv, and could start sooner for Europe PMC given its existing `license` field.

## Assumptions and dependencies

- **Provider availability.** PriorisMCP depends on arXiv's and Europe PMC's APIs remaining available and stable in shape; an outage or breaking API change in either is an external dependency, not something this SRS specifies behaviour for beyond the error semantics already described per-capability (see [Functional requirements](03-functional-requirements.md)).
- **Consent for heavier operations rests with the MCP client.** PriorisMCP assumes the calling MCP client (the LLM, or the human behind it) is the point where consent for a heavier operation is decided — e.g. `parse_full_text` failing explicitly rather than silently triggering `fetch_full_text` (see [Architecture → `parse_full_text`](01-architecture.md#parse_full_text)) exists specifically so that decision point remains with the caller, not PriorisMCP.
- **Content licensing is separate from rate-limit terms of use.** The rate limits documented in [References](#references) govern *how often* PriorisMCP may call arXiv/Europe PMC; they say nothing about the licensing terms attached to the content itself once fetched. arXiv and Europe PMC articles carry their own (often per-article) licences — some permissive, some not — governing redistribution of full text. v1 persists fetched full text indefinitely via the [storage abstraction](02-storage.md) as a caching/de-duplication mechanism for the same MCP client that already fetched it; this SRS does not currently address longer-term redistribution of persisted content beyond that use — see [Out of scope for v1 → Redistribution-policy-aware persistence](#out-of-scope-for-v1) — and this should be revisited before any feature that shares persisted content beyond the fetching client is considered.

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
- [Interface specification](06-interface-specification.md) — exact MCP wire-level input/output schemas for every v1 tool, grounded in the arXiv and Europe PMC APIs.
- [Test specification](07-test-specification.md) — verification/acceptance criteria per capability, grounded in the interface specification's schemas.
