---
status: accepted
date: 2026-07-20
---

# ADR-00003: DOI routing to an unsupported provider fails, rather than scraping

## Context

[`research_resolve_identifier`](../01-architecture.md#identifier-routing-grouping-level) resolves a DOI via the DOI system (a `doi.org`/Crossref redirect) before any provider-specific logic runs. The resulting landing domain sometimes belongs to neither arXiv nor Europe PMC — a publisher's own site, for instance.

## Decision

Routing fails with a hard **`unsupported_provider`** error.

## Alternatives considered

- **Scrape the landing page** for whatever metadata/content is reachable — rejected on two grounds: it's a materially different, unvetted capability that was never checked against that publisher's terms of use the way arXiv's and Europe PMC's documented APIs were; and it would produce a partial, silently-degraded result (metadata scraped, full text unreachable behind a paywall) rather than a predictable, typed failure.

## Consequences

A caller gets one clear, typed failure instead of an inconsistent partial result. Broader publisher support requires deliberately adding a vetted provider, not falling through to generic scraping. This decision doubles as a security boundary, not just a terms-of-use one — see [Security → Untrusted identifiers must not drive unconstrained outbound requests](../05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests).

## Referenced from

- [Architecture → Identifier routing (grouping-level)](../01-architecture.md#identifier-routing-grouping-level)
