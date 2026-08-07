---
icon: lucide/flask-conical
---

# Test specification

This page states verification/acceptance criteria per capability, grounded in the exact wire-level shapes in [Interface specification](06-interface-specification.md) — a test can't check a response field that isn't yet named, which is why this page follows rather than precedes it. These criteria are what the project's test suite (see `tests/` and `CLAUDE.md`) should be judged against, alongside the existing `just test-coverage` gate (100% line coverage required).

## Conventions

Each criterion below is phrased as a "given/must" statement intended to map onto one or more automated tests, not as prose describing behaviour already stated elsewhere — [Functional requirements](03-functional-requirements.md), [Non-functional requirements](04-non-functional-requirements.md), and [Security](05-security.md) remain the source of *why*; this page states what a test asserts to confirm it.

Tests must not perform live network calls against arXiv or Europe PMC: both to respect the rate limits this spec itself requires the server to honour, and for deterministic, offline CI. HTTP responses at the upstream layer are stubbed to match the exact Atom/JSON shapes confirmed live in [Interface specification](06-interface-specification.md). This is separate from the existing in-process `Client`/`FastMCP` pattern described in `CLAUDE.md`, which covers the MCP protocol layer, not the upstream HTTP calls a tool makes internally.

The local filesystem source has no upstream to stub — its tests instead call `research_localfile_fetch_full_text` with base64-encoded content built directly in the test, with no filesystem fixtures needed since the tool no longer reads from server-side disk at all.

A criterion below phrased as "fails with `<category>`" (e.g. `not_found`, `invalid_request`) means a test asserting the exception type that category names is raised — see [Interface specification → Conventions → Errors](06-interface-specification.md#conventions) for the category-to-exception mapping. At the provider/routing-function level (calling e.g. `resolve_research_identifier` or a provider method directly, not through an MCP `Client`), assert `pytest.raises(<the specific exception type>)`. At the full MCP `Client`/`FastMCP` level (see `CLAUDE.md`), FastMCP collapses every uncaught exception into one opaque `fastmcp.exceptions.ToolError`, so the assertion there is `pytest.raises(ToolError)` regardless of which category triggered it — this page does not pin down `ToolError`'s message text as a stable, asserted contract.

## arXiv acceptance criteria

| Tool | Criteria |
|---|---|
| `research_arxiv_search` | A valid query with default parameters returns `results` (the documented metadata shape) and a `total_results` integer. A zero-hit query returns `results: []` and `total_results: 0` — not an error. A request with `max_results` over 2000, or `start + max_results` over 30000, fails with a validation error from `research_arxiv_search` itself, without a call reaching arXiv. |
| `research_arxiv_list_top_n` | Returns up to `n` records matching `include_categories` (`AND`-combined) minus `exclude_categories` (`ANDNOT`-combined), ordered most-recently-submitted first. Requesting more than the query actually has returns however many exist, without erroring. An empty `include_categories` fails with `invalid_request` before any outbound call. Duplicate entries in either list are deduplicated before the query is built. |
| `research_arxiv_fetch_metadata` | One or more valid `arxiv_ids` (with or without version suffix) each return a metadata record in `results`. A batch mixing valid and invalid IDs returns records only for the valid ones, with the rest in `not_found` — the call as a whole must not fail. An all-invalid batch returns `results: []` and every requested ID in `not_found`. |
| `research_arxiv_fetch_full_text` | `format: pdf` always succeeds (every arXiv submission has a PDF). `format: html` on an item with no native HTML rendering fails with `format_unavailable`, not `not_found`. An unversioned `arxiv_id` resolves to its current canonical, version-pinned form before being persisted or looked up. A second call for an already-persisted `(id, format)` returns `served_from_storage: true` and does not issue a second outbound fetch. An unrecognised `arxiv_id` fails with `not_found`. |
| `research_arxiv_parse_full_text` | An already-fetched `(arxiv_id, format)` returns `markdown` and `resource_uri`. A never-fetched `(arxiv_id, format)` fails with `not_found` and must not trigger a `fetch_full_text` call — assert no outbound network request occurs. For `format="pdf"`: a `page` request returns `total_pages` and a `page_range` consistent with the per-document manifest, and `offset` becomes relative to that page's start. `page` passed with `format="html"` fails with `invalid_request` before any storage read. |

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

## Local filesystem acceptance criteria

| Tool | Criteria |
|---|---|
| `research_localfile_fetch_full_text` | Valid, within-size-limit base64-encoded PDF content succeeds, returning a caller-facing `id`, `served_from_storage: false`, and a `resource_uri`. A second call with the same `content_base64` and unchanged content returns `served_from_storage: true` and the **same** `id` as the first call, without a second `write`. A second call with changed content (different bytes) returns a **different** `id`, and the first call's `id` remains independently readable afterward. `content_base64` that is not valid base64 fails with `invalid_request`. `content_base64` whose *encoded* length alone implies decoded content over `PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES` fails with `file_too_large` before any decoding occurs. Content that decodes to something within the pre-decode ceiling but still over the cap once decoded (an edge case of base64's 3-byte/4-char rounding) also fails with `file_too_large`, via the post-decode length check. Content that decodes but is not actually a PDF (verified via content, not any `filename` hint's extension — e.g. a `.pdf`-named non-PDF payload) fails with `invalid_request`. |
| `research_localfile_parse_full_text` | An already-fetched `id` returns `markdown` and `resource_uri`, using the same PDF parser backend as `research_arxiv_parse_full_text`. An unrecognised or never-fetched `id` fails with `not_found` and must not trigger `fetch_full_text` — assert no filesystem read occurs beyond the catalogue lookup. A `page` request returns `total_pages` and a consistent `page_range`, same as the arXiv PDF case. |

**No rate limiting:** confirm neither tool is routed through a per-provider outbound queue and that no outbound network request occurs for either — per [Architecture → Local filesystem source](01-architecture.md#local-filesystem-source).

**Chunked upload (`research_localfile_begin_upload` / `research_localfile_upload_chunk` / `research_localfile_finalize_upload`):**

- Chunked upload happy path: `begin_upload` → sequential `upload_chunk` calls → `finalize_upload` produces the same result shape as `fetch_full_text`, including `served_from_storage` dedup.
- `upload_chunk` with a skipped or repeated `index` fails `invalid_request`.
- `upload_chunk`/`finalize_upload` on an unknown or TTL-expired `session_id` fails `not_found`.
- `upload_chunk` with a chunk over `PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CHUNK_BYTES` fails `file_too_large`; cumulative total over `PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES` fails `file_too_large` without a full decode.
- `begin_upload` at `PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CONCURRENT_SESSIONS` open sessions fails `invalid_request`; succeeds again once one is finalized or expires.
- `finalize_upload` with zero chunks uploaded fails `invalid_request`.
- `finalize_upload` on reassembled content that doesn't sniff as a PDF fails `invalid_request`, proving `_validate_and_persist` applies identically to both paths.

## Storage management acceptance criteria

| Tool | Criteria |
|---|---|
| `research_list_fetched` | With no filters, returns entries for every persisted `(provider, identifier, format, artefact)` across all three sources. A `provider` filter returns only that provider's entries; adding a `format` filter narrows further. Returns `entries: []` when nothing matches, not an error. Never triggers a fetch or parse — assert no outbound network request or filesystem read of source content occurs. Reflects `catalogue.sqlite`, not a live directory glob — a manually-corrupted/removed on-disk file without a corresponding catalogue update is not required to be reflected (catalogue is the source of truth). |
| `research_delete_fetched` | A batch naming one or more `(provider, identifier, format, artefact)` entries currently in storage removes each (subsequent `research_list_fetched`/`parse_full_text` no longer finds them) and reports them in `deleted`. A batch mixing present and already-absent entries deletes the present ones and reports the rest in `not_found` — the call as a whole must not fail. An all-absent batch returns `deleted: []` and every requested entry in `not_found`. Deleting a `localfile` entry only removes the storage abstraction's own copy — there is no server-side original to leave untouched, since the source content was caller-sent, not read from server disk. Deleting `artefact="document"` leaves `artefact="markdown"` (and vice versa) readable and independently listed — no cross-artefact cascade. `artefact="all"` removes both, and removes the document-hash directory too once it was the last format directory for that document. |
| `research_search_fetched` | A query matching a persisted chunk (or, for a document with none, a leaf) returns ranked `matches`, each naming the specific section/page it matched rather than just the document; a query matching nothing returns `matches: []`, not an error. A query matching both an outer section and one of its own nested subsections returns both as separate matches, not deduplicated. An `identifier`+`provider`-scoped query only returns matches from that one document, even when other stored documents contain the same term. A match's `offset` equals the matched entry's position in the document's own coordinate space (verified by passing it straight into `parse_full_text`'s `offset`), not any FTS5-internal offset. Never triggers a fetch or parse. Querying before anything has been persisted at all returns `matches: []`, not an error about a missing index. |

## Storage layer acceptance criteria

Per [Storage](02-storage.md), below the level of any single MCP tool:

- Two different formats fetched for the same `(provider, canonical identifier)` (e.g. arXiv `pdf` and `html`) land under the same `<document-hash>` directory, in separate `<format>/` subdirectories, sharing the same `manifest.sqlite` — asserted by fetching both and checking they share a document-hash prefix, and that parsing the second format adds rows to the existing manifest rather than creating a second one.
- `manifest.sqlite` gets a `kind="leaf"` row per PDF page (or a single trivial row for `format="html"`/Europe PMC XML, which have no page concept); each row's `{start, length}` span, sliced out of `markdown`, reconstructs exactly that page's own text, and the full set of leaf rows for one `(document, format)` has no gaps or overlaps.
- A parsed document whose rendered Markdown contains ATX headings gets `kind="chunk"` rows from the heading-walker: one row per heading, each row's span running to the next heading at the same or shallower level. A document with nested headings (e.g. `##` inside `#`) produces overlapping rows, with the outer section's span containing the inner subsection's span. A `#`-prefixed line inside a fenced code block must not produce a spurious chunk row.
- A document whose parser couldn't recover structure (or whose rendered Markdown has no headings at all) has zero `chunk` rows and only `leaf` rows; `research_search_fetched` against it still returns leaf-scoped matches, not an error (see [Storage management acceptance criteria](#storage-management-acceptance-criteria) above).
- `catalogue.sqlite` reflects every `write`/`delete` exactly once; a unique-constraint violation on `(provider, canonical_identifier, format, artefact)` — simulating two concurrent local writer processes racing to write the same key — resolves via `INSERT ... ON CONFLICT`, not a crash or a duplicate row, and `list`/`exists`/`find_canonical_identifier` report exactly one live entry for that key afterward.
- The FTS5 index (`search.sqlite3`) can be deleted and is transparently rebuilt from `catalogue.sqlite`/`manifest.sqlite` + `markdown` files on next use, with no error surfaced to the caller and no change in `research_search_fetched` results before/after.
- `search.sqlite3` is a separate file from `catalogue.sqlite` and each document's `manifest.sqlite`: deleting or corrupting `search.sqlite3` alone must not affect `research_list_fetched`, `parse_full_text`, or `fetch_full_text`, and recovering from it never requires touching either of the other two files (rebuild only).
- A pre-existing flat `<hash>`/`<hash>.json` store (the layout that predates this design) migrates into `documents/<document-hash>/<format>/...` plus a freshly-built `catalogue.sqlite` and per-document `manifest.sqlite` files, and the migration is a no-op (idempotent) when run a second time against an already-migrated store, and safe against a store that's a mix of old and new layout.

## `SearchIndex` acceptance criteria

Below the level of `research_search_fetched`, per [Architecture → `SearchIndex`](01-architecture.md#searchindex):

- `index_entries` for a `(provider, identifier, format)` document replaces its previously-indexed rows wholesale — re-parsing a document with a different chunking `scheme`, or after a re-parse changes chunk boundaries, leaves no stale rows from the prior indexing pass searchable afterward.
- `remove_document` removes every indexed row for a `(provider, identifier, format)` document; a subsequent search for a term that only appeared in that document returns no matches from it, while matches from other documents are unaffected.
- Deleting a document's `markdown` artefact via `research_delete_fetched` triggers `remove_document` for it — no orphaned FTS5 rows survive a deletion that removed their source content.

## `ParserBackend` acceptance criteria

Below the level of any MCP tool, per [Architecture → `parse_full_text`](01-architecture.md#parse_full_text):

- `to_markdown()` returns `{"markdown": str, "leaf_spans": [...]}` for every backend, not a bare string; each `leaf_spans` entry's `{start, length}`, sliced out of `markdown`, reconstructs exactly that leaf's own text.
- `LiteParsePdfBackend`'s leaf spans come from offsets tracked while joining pages together, not from searching the assembled blob for a separator token afterward — a page whose own content happens to contain the joiner text must not corrupt span boundaries.
- `JatsXsltMarkdownBackend` and the HTML backend always return a single `leaf_spans` entry spanning the whole blob.
- The HTML-to-Markdown backend's existing substring-based assertions (e.g. a heading or bold-text marker present in the output) continue to hold verbatim after the `html-to-markdown` library swap; only the backend's class/module name and the monkeypatched conversion-function name change, not the assertions themselves.
- A JATS `<alternatives>` wrapper containing both `tex-math` and `mml:math` for the same formula renders only the `tex-math` content, with no duplicated or garbled token from the suppressed `mml:math` alternative.
- A JATS section nested deeper than a subsection renders at a heading level strictly greater than its parent's, not aliased to the same level (the depth-mapping fix's regression test).

## Extracted PDF images acceptance criteria (optional capability)

Per [Storage → Future: extracted PDF images](02-storage.md#future-extracted-pdf-images):

- With `PRIORIS_MCP_PDF_EXTRACT_IMAGES` unset/`False` (the default), parsing a PDF containing images produces no `kind="image"` manifest rows and registers no image resources, even though the Markdown output still contains an inline image placeholder — behaviour identical to a build without this capability at all.
- With it enabled, parsing the same PDF produces one `kind="image"` manifest row per extracted image, each resolvable to a persisted artefact via `StorageBackend`, and each readable as its own MCP resource.
- Deleting the `markdown` artefact for a document with extracted images (or `artefact="all"`) also removes its image artefacts and manifest rows — a subsequent `research_list_fetched` or image-resource read finds nothing.
- Two images with identical bytes (the parser's own dedup) are reflected via `duplicate_of` rather than being persisted twice.

## `research_resolve_identifier` acceptance criteria

- An arXiv ID input resolves directly to the arXiv provider, with no DOI redirect round-trip.
- A Europe PMC identifier input resolves directly to the Europe PMC provider, with no DOI redirect round-trip.
- A DOI that redirects (via `doi.org`/Crossref) to an `arxiv.org` domain resolves with `provider: "arxiv"` and the canonical arXiv identifier.
- A DOI that redirects to a `europepmc.org`/NCBI PMC domain resolves with `provider: "europepmc"`.
- A DOI that redirects to any other domain fails with `unsupported_provider` — and this is the load-bearing security assertion (see [Security](05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests)): the test must assert that **no HTTP request is made to that landing page at all**, not merely that the call failed. The allowlist check happens before any request to the resolved URL.

## Resources acceptance criteria

- `research://{provider}/{identifier}/{format}/fulltext` returns the persisted content once the corresponding `fetch_full_text` call has been made for that exact key; before that, reading it is a plain not-found, not an error requiring special handling.
- `research://{provider}/{identifier}/{format}/markdown` behaves the same way relative to `parse_full_text`.
- Reading either of the above resources must never itself trigger a fetch or a parse — assert no outbound network request or parse executes as a side effect of a resource read.
- `research://arxiv/categories` returns only leaf category codes (no archive/group nodes with children of their own), each with its derived `code` and its `name` taken from the OAI-PMH response, sorted by `code`.
- `research://localfile/{id}/pdf/fulltext` and `research://localfile/{id}/pdf/markdown` behave identically to the arXiv/Europe PMC cases above, keyed on the caller-facing `id` returned by `research_localfile_fetch_full_text` — reading either never re-triggers a fetch or a parse.
- No per-item metadata resource template exists — confirm the registered resource list contains exactly `fulltext`, `markdown`, and `research://arxiv/categories`, per [Functional requirements → Resources](03-functional-requirements.md#resources).

## Cross-cutting: concurrency

Per [Non-functional requirements](04-non-functional-requirements.md#concurrency):

- Concurrent calls to the same provider's tools are serialised at the outbound-request level (restates the per-tool rate-limiting criteria above as the one general property they share).
- Two concurrent `fetch_full_text` calls for the identical `(provider, canonical identifier, format)` key result in exactly one outbound network fetch; the second call waits and receives the same result rather than starting a redundant download.
- Two concurrent `parse_full_text` calls for the identical `(provider, identifier, format)` key result in exactly one parse execution; the second call waits and receives the same Markdown rather than re-parsing.
- Two concurrent `research_localfile_fetch_full_text` calls for the same `content_base64` result in exactly one `write` and both calls returning the same caller-facing `id` — the content-hash check-then-write must not race the same way [Storage must de-duplicate in-flight work](04-non-functional-requirements.md#storage-must-de-duplicate-in-flight-work-not-just-completed-work) already requires for network-fetched content.

## Cross-cutting: security

Per [Security](05-security.md):

- The DOI-allowlist criterion under `research_resolve_identifier` above is the primary test for [Untrusted identifiers must not drive unconstrained outbound requests](05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests).
- The invalid-base64 criterion under `research_localfile_fetch_full_text` above is the primary test for [Local filesystem access means access to the caller's own content, not the server's disk](05-security.md#local-filesystem-access-means-access-to-the-callers-own-content-not-the-servers-disk) — there is no path-containment behaviour left to test since there is no server-side path.
- The non-PDF-content and oversized-payload criteria under `research_localfile_fetch_full_text` above are the primary tests for the local-file additions to [Fetched content is untrusted input to `parse_full_text`](05-security.md#fetched-content-is-untrusted-input-to-parse_full_text) — content sniffing and both the pre-decode and post-decode size checks must be enforced before anything is persisted.
- `parse_full_text`, given a malformed or pathological document (a corrupt PDF, a deeply nested or oversized HTML/XML document), must fail with a bounded, typed error within a defined time/resource budget rather than hang or crash the process. This page does not pin down the exact budget (timeout, max size) — that's an implementation detail — but a test must exist asserting *some* bound is enforced, not merely that valid documents parse correctly. This applies to a locally-supplied PDF that passes the format sniff but is otherwise pathological (e.g. a valid-looking header wrapping a decompression bomb), not just to network-fetched documents.
- Per [Security → PriorisMCP's own HTTP ingress surface](05-security.md#priorismcps-own-http-ingress-surface): with no environment override, a `streamable-http`/`http`-transport server must (a) bind to `localhost`, not a wildcard interface, and (b) reject a CORS preflight/request from an arbitrary non-localhost `Origin` — `PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS` must not default to `["*"]`. These are ASGI/HTTP-layer assertions (e.g. via an ASGI test client against `http_app()`), distinct from the in-process MCP `Client`/`FastMCP` pattern used for the tool-level tests above.

## Test implementation notes

- `just test-coverage`'s 100% line-coverage gate applies to all capability code as it lands; genuinely unreachable branches use `# pragma: no cover` / `# pragma: lax no cover` per `CLAUDE.md`, rather than tests contorted to hit them.
- Authenticated-source test criteria are out of scope, following [Security → Authenticated sources are explicitly deferred](05-security.md#authenticated-sources-are-explicitly-deferred-not-silently-assumed).
