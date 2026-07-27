---
icon: lucide/flask-conical
---

# Test specification

This page states verification/acceptance criteria per capability, grounded in the exact wire-level shapes in [Interface specification](06-interface-specification.md) — a test can't check a response field that isn't yet named, which is why this page follows rather than precedes it. These criteria are what the project's test suite (see `tests/` and `CLAUDE.md`) should be judged against, alongside the existing `just test-coverage` gate (100% line coverage required).

## Conventions

Each criterion below is phrased as a "given/must" statement intended to map onto one or more automated tests, not as prose describing behaviour already stated elsewhere — [Functional requirements](03-functional-requirements.md), [Non-functional requirements](04-non-functional-requirements.md), and [Security](05-security.md) remain the source of *why*; this page states what a test asserts to confirm it.

Tests must not perform live network calls against arXiv or Europe PMC: both to respect the rate limits this spec itself requires the server to honour, and for deterministic, offline CI. HTTP responses at the upstream layer are stubbed to match the exact Atom/JSON shapes confirmed live in [Interface specification](06-interface-specification.md). This is separate from the existing in-process `Client`/`FastMCP` pattern described in `CLAUDE.md`, which covers the MCP protocol layer, not the upstream HTTP calls a tool makes internally.

## arXiv acceptance criteria

| Tool | Criteria |
|---|---|
| `research_arxiv_search` | A valid query with default parameters returns `results` (the documented metadata shape) and a `total_results` integer. A zero-hit query returns `results: []` and `total_results: 0` — not an error. A request with `max_results` over 2000, or `start + max_results` over 30000, fails with a validation error from `research_arxiv_search` itself, without a call reaching arXiv. |
| `research_arxiv_list_top_n` | Returns up to `n` records for `category`, ordered most-recently-submitted first. Requesting more than the category actually has returns however many exist, without erroring. |
| `research_arxiv_fetch_metadata` | One or more valid `arxiv_ids` (with or without version suffix) each return a metadata record in `results`. A batch mixing valid and invalid IDs returns records only for the valid ones, with the rest in `not_found` — the call as a whole must not fail. An all-invalid batch returns `results: []` and every requested ID in `not_found`. |
| `research_arxiv_fetch_full_text` | `format: pdf` always succeeds (every arXiv submission has a PDF). `format: html` on an item with no native HTML rendering fails with `format_unavailable`, not `not_found`. An unversioned `arxiv_id` resolves to its current canonical, version-pinned form before being persisted or looked up. A second call for an already-persisted `(id, format)` returns `served_from_storage: true` and does not issue a second outbound fetch. An unrecognised `arxiv_id` fails with `not_found`. |
| `research_arxiv_parse_full_text` | An already-fetched `(arxiv_id, format)` returns `markdown` and `resource_uri`. A never-fetched `(arxiv_id, format)` fails with `not_found` and must not trigger a `fetch_full_text` call — assert no outbound network request occurs. |

**Rate limiting:** N concurrent calls to any arXiv tool must be observed, at the outbound-request level, spaced at least 3 seconds apart — never two in flight at once — regardless of how many tool calls were issued together. A stubbed 429 response must not fail the call immediately: the queue retries with spacing doubling — 3s, 6s, 12s, 24s, 48s, ... (per [Non-functional requirements → Rate-limit breaches](04-non-functional-requirements.md#rate-limit-breaches-are-handled-inside-the-providers-queue-not-by-the-caller)) — and the tool call succeeds once a stubbed retry succeeds within the default 60-second total backoff budget. A sequence of stubbed 429s that exhausts that budget must surface `rate_limited` to the caller, not hang or retry indefinitely. A stubbed timeout, connection error, or 5xx (not a 429) must instead surface `provider_unavailable` on the first attempt, with no retry observed at all (per [Non-functional requirements → `provider_unavailable` failures are not retried](04-non-functional-requirements.md#provider_unavailable-failures-are-not-retried)).

## Europe PMC acceptance criteria

| Tool | Criteria |
|---|---|
| `research_europepmc_search` | A valid query returns `results` and `hit_count`. `next_cursor_mark` is present only when a further page exists, and re-invoking with that value returns the next page (no overlap or duplication with the previous page), not absent/empty on the last page. |
| `research_europepmc_fetch_metadata` | One or more `identifiers` (bare PMCID or `{source}:{id}`) return a record per recognised identifier in `results`, with the rest in `not_found`. Verified for a same-source batch per the interface spec's live confirmation; a mixed-source batch should be confirmed against a live call during implementation, since the interface spec flags this as untested. |
| `research_europepmc_fetch_full_text` | An identifier with `inEPMC: Y` and a resolvable `pmcid` returns the XML full text via the `fullTextXML` endpoint. An identifier with `inEPMC: N`, or with no `pmcid`, fails with `format_unavailable` — and must not attempt any URL from `fullTextUrlList` (those can point off-domain; see [Security](05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests)). An identifier given as `MED:{pmid}` resolves to a `pmcid` via `fetch_metadata` first. An unrecognised identifier fails with `not_found`. |
| `research_europepmc_parse_full_text` | An already-fetched `(identifier, xml)` returns `markdown` and `resource_uri`. A never-fetched identifier fails with `not_found` and must not trigger a fetch. |

**Rate limiting:** the same 3-seconds-apart, never-concurrent criterion as arXiv above, applied to Europe PMC's self-imposed limit — including the same doubling-backoff-then-`rate_limited` behaviour on a stubbed 429, and the same immediate, un-retried `provider_unavailable` on a stubbed timeout, connection error, or 5xx.

There is no `research_europepmc_list_top_n` to test against — confirm its absence from the registered tool list, matching [Functional requirements](03-functional-requirements.md#europe-pmc-tools).

## `research_resolve_identifier` acceptance criteria

- An arXiv ID input resolves directly to the arXiv provider, with no DOI redirect round-trip.
- A Europe PMC identifier input resolves directly to the Europe PMC provider, with no DOI redirect round-trip.
- A DOI that redirects (via `doi.org`/Crossref) to an `arxiv.org` domain resolves with `provider: "arxiv"` and the canonical arXiv identifier.
- A DOI that redirects to a `europepmc.org`/NCBI PMC domain resolves with `provider: "europepmc"`.
- A DOI that redirects to any other domain fails with `unsupported_provider` — and this is the load-bearing security assertion (see [Security](05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests)): the test must assert that **no HTTP request is made to that landing page at all**, not merely that the returned error code is correct. The allowlist check happens before any request to the resolved URL.

## Resources acceptance criteria

- `research://{provider}/{identifier}/{format}/fulltext` returns the persisted content once the corresponding `fetch_full_text` call has been made for that exact key; before that, reading it is a plain not-found, not an error requiring special handling.
- `research://{provider}/{identifier}/{format}/markdown` behaves the same way relative to `parse_full_text`.
- Reading either resource must never itself trigger a fetch or a parse — assert no outbound network request or parse executes as a side effect of a resource read.
- No metadata resource template exists — confirm the registered resource list contains only the two templates above, per [Functional requirements → Resources](03-functional-requirements.md#resources).

## Cross-cutting: concurrency

Per [Non-functional requirements](04-non-functional-requirements.md#concurrency):

- Concurrent calls to the same provider's tools are serialised at the outbound-request level (restates the per-tool rate-limiting criteria above as the one general property they share).
- Two concurrent `fetch_full_text` calls for the identical `(provider, canonical identifier, format)` key result in exactly one outbound network fetch; the second call waits and receives the same result rather than starting a redundant download.
- Two concurrent `parse_full_text` calls for the identical `(provider, identifier, format)` key result in exactly one parse execution; the second call waits and receives the same Markdown rather than re-parsing.

## Cross-cutting: security

Per [Security](05-security.md):

- The DOI-allowlist criterion under `research_resolve_identifier` above is the primary test for [Untrusted identifiers must not drive unconstrained outbound requests](05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests).
- `parse_full_text`, given a malformed or pathological document (a corrupt PDF, a deeply nested or oversized HTML/XML document), must fail with a bounded, typed error within a defined time/resource budget rather than hang or crash the process. This page does not pin down the exact budget (timeout, max size) — that's an implementation detail — but a test must exist asserting *some* bound is enforced, not merely that valid documents parse correctly.
- Per [Security → PriorisMCP's own HTTP ingress surface](05-security.md#priorismcps-own-http-ingress-surface): with no environment override, a `streamable-http`/`http`-transport server must (a) bind to `localhost`, not a wildcard interface, and (b) reject a CORS preflight/request from an arbitrary non-localhost `Origin` — `PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS` must not default to `["*"]`. These are ASGI/HTTP-layer assertions (e.g. via an ASGI test client against `http_app()`), distinct from the in-process MCP `Client`/`FastMCP` pattern used for the tool-level tests above.

## Test implementation notes

- `just test-coverage`'s 100% line-coverage gate applies to all capability code as it lands; genuinely unreachable branches use `# pragma: no cover` / `# pragma: lax no cover` per `CLAUDE.md`, rather than tests contorted to hit them.
- Authenticated-source test criteria are out of scope, following [Security → Authenticated sources are explicitly deferred](05-security.md#authenticated-sources-are-explicitly-deferred-not-silently-assumed).
