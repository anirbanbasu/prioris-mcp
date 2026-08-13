---
status: accepted
date: 2026-08-12
---

# ADR-00017: Auto-fetch of OA PDF URLs is rejected, for now

## Context

A `search.semantic` hit that isn't already backed by an arXiv/Europe PMC identifier may still have an open-access PDF URL surfaced via OpenAlex's OA-locations data (OpenAlex reports OA locations for roughly half its indexed corpus). Whether PriorisMCP should fetch that PDF server-side, closing the loop without a user round-trip, needed a decision — not left ambiguous.

## Decision

PriorisMCP does not fetch OA PDF URLs server-side. It surfaces the first OA-locations entry with a populated `pdf_url` (preferring published > accepted > submitted version, looping the full OA-locations list rather than trusting only OpenAlex's own single `best_oa_location` pick) as a convenience, and leaves fetching to the caller.

## Alternatives considered

- **Auto-fetch the surfaced OA PDF URL server-side** — in favour: closes the loop for a large share of hits without a user round-trip, and is superficially symmetric with what arXiv/Europe PMC providers already do (fetch from a known URL). Rejected, decisively: arXiv/Europe PMC are two sanctioned, stable, structured APIs, built and rate-limited for exactly this kind of automated access. "Any OA PDF URL OpenAlex happens to point at" is a long tail of publisher sites and repository mirrors with no equivalent sanction, uneven ToS footing, and real link-rot/mis-flagged-OA risk — server-side fetching of arbitrary external URLs is an SSRF-shaped trust surface this project doesn't currently have. It also doesn't buy much: the manual-upload fallback isn't a stub — the local-file+OCR ingestion path already exists and works today.
- **Live-validating the surfaced `pdf_url` (HEAD/MIME-type check) before surfacing it** — considered and set aside for the same reason, not designed away entirely: confirming a link resolves before surfacing it would still mean the server contacts an arbitrary external host. Flagged in [#34](https://github.com/anirbanbasu/prioris-mcp/issues/34); validation stays a presence/shape check only.

## Consequences

A `search.semantic` hit with no known-provider route and no usable OA link falls through to the manual upload fallback: the user sources the PDF themselves and uploads it via the existing local-file ingestion path. General external-URL fetching, if it's ever adopted, is its own future debate (allowlisting, SSRF protection, ToS handling), not folded into discovery.

## Referenced from

- [Discovery → Auto-fetch of OA PDF URLs: rejected for now](../02-discovery.md#auto-fetch-of-oa-pdf-urls-rejected-for-now)
