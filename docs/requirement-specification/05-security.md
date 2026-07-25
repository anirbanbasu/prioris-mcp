---
icon: lucide/shield
---

# Security

This page covers security requirements — another cross-cutting quality alongside [Non-functional requirements](04-non-functional-requirements.md), but broken out into its own page rather than folded in there, since v1's security surface is concrete enough (and distinct enough in kind from concurrency) to warrant its own visibility rather than being one subsection among others.

v1 targets unauthenticated, open-access providers only (see [SRS overview → Scope](index.md#v1)), so this page is **not** about authentication or credential handling — it's about the concrete risks that exist even without any auth: an MCP client-supplied identifier driving an outbound network request, and externally-sourced content flowing into a parser.

## Untrusted identifiers must not drive unconstrained outbound requests

[Architecture → Identifier routing](01-architecture.md#identifier-routing-grouping-level) already requires DOI resolution to fail with an "unsupported provider" error rather than fall back to scraping an arbitrary landing page — that decision was made for terms-of-use reasons, but it also happens to be a security boundary: `research_resolve_identifier` takes a caller-supplied identifier and, for a DOI, follows a redirect (via `doi.org`/Crossref) to a URL it does not control in advance.

This must be implemented as a strict allowlist on the resulting domain (arxiv.org, europepmc.org/NCBI PMC) checked **before** any further request is made against it, not as "fetch the landing page, then decide what to do with it." A caller-influenced identifier must never be able to cause PriorisMCP to issue a request to an arbitrary host — including internal/private network addresses — as a side effect of resolution. The existing functional requirement (fail closed with "unsupported provider") already produces the right behaviour; this page states the security rationale for why that must hold even if the functional requirement is later relaxed (e.g. to add a new supported provider) — any addition must extend the allowlist deliberately, not open resolution up to whatever a redirect happens to return.

## Fetched content is untrusted input to `parse_full_text`

Full text persisted by `fetch_full_text` originates from external sources and is later fed into `parse_full_text`. Even though v1's providers (arXiv, Europe PMC) are trusted in the sense of being deliberately chosen, documented APIs, the documents they serve are third-party content (uploaded by their own authors/publishers) that PriorisMCP does not control the shape of.

`parse_full_text` must treat this content as untrusted input: malformed, unusually large, or pathological documents (e.g. a PDF crafted to be maximally expensive to parse) must fail as a bounded, typed error rather than crash the server process or hang it indefinitely. This is a robustness requirement on the parsing path specifically, distinct from the general error-handling already described for missing content in [Architecture → `parse_full_text`](01-architecture.md#parse_full_text).

## Authenticated sources are explicitly deferred, not silently assumed

Credential/secret handling for authenticated sources (e.g. Semantic Scholar) is out of scope for v1 (see [SRS overview → Out of scope for v1](index.md#out-of-scope-for-v1)). This page notes it explicitly so it isn't forgotten: adding an authenticated provider later will need its own security requirements (credential storage, no secrets in logs or cached responses, per-source scoping) written before that provider is built, not assumed to fall out of the existing unauthenticated design.

## Next

- [Interface specification](06-interface-specification.md) and [Test specification](07-test-specification.md) — deferred until the provider APIs are read; the test specification should eventually include acceptance criteria for the requirements on this page (e.g. a malformed-document parse must fail cleanly, not hang).
