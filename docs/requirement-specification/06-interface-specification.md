---
icon: lucide/plug
---

# Interface specification

This page states the exact wire-level input/output contract for every v1 MCP tool and resource named in [Functional requirements](03-functional-requirements.md), grounded in the arXiv and Europe PMC APIs as they actually behave — verified live against `export.arxiv.org`, `arxiv.org`, and `www.ebi.ac.uk/europepmc` while drafting this page, not recalled from memory. Where a detail depends on upstream API behaviour, the upstream call it derives from is stated alongside it.

The [local filesystem tools](#local-filesystem) and [storage management tools](#storage-management) below have no upstream API to ground against — their exact shape is a PriorisMCP design decision, not something confirmed live against a third party, so they carry no "underlying call" line.

## Conventions

**Identifiers.** An arXiv identifier is a bare or version-suffixed arXiv ID (`2106.09685` or `2106.09685v2`), or the older, pre-2007 category-prefixed form (`hep-th/9901001` or `hep-th/9901001v1`) — both shapes remain valid arXiv identifiers and both are recognised by `research_resolve_identifier`'s routing. A Europe PMC identifier is the composite `{source}:{id}` form Europe PMC itself uses internally (e.g. `MED:26551875`), with a bare PMCID (`PMC4767193`) also accepted as shorthand since it's unambiguous on its own (implicitly `SRC:PMC`).

**Errors.** A failure condition below is represented server-side by one of the typed exceptions in `src/prioris_mcp/errors.py`, raised directly by a provider (or by `research_delete_fetched`'s own validation) and left to propagate uncaught out of the tool method. FastMCP converts it into an MCP `ToolError` whose message is `Error calling tool '<tool_name>': <detail>` (`<detail>` is the exception's own `str()`, e.g. `arXiv identifier not recognised: 9999.99999`) — **there is no structured, machine-readable error field in the response**; a caller cannot branch on a code and must treat any `ToolError` as an opaque failure with a human-readable message. (`research_resolve_identifier`'s **`unsupported_provider`** case remains the one deliberately load-bearing exception: see [Security → Untrusted identifiers](05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests) for why the request must never reach the disallowed domain in the first place, independent of how the failure is reported.) The category names below (`not_found`, `format_unavailable`, ...) label which exception type produces a given failure, purely as a shared vocabulary for this page and the per-tool sections that follow — they are not literal response fields. Categories used across this page: `not_found` (`NotFoundError` — identifier not recognised by the provider, or requested format not persisted — see [Architecture → `parse_full_text`](01-architecture.md#parse_full_text); `research_localfile_fetch_full_text` never raises it since it doesn't resolve anything, only decodes what the caller sent), `format_unavailable` (`FormatUnavailableError` — identifier is valid but doesn't offer the requested format — e.g. an older arXiv paper with no native HTML rendering), `unsupported_provider` (`UnsupportedProviderError` — see [Architecture → Identifier routing](01-architecture.md#identifier-routing-grouping-level)), `invalid_request` (`InvalidRequestError` — caller-supplied arguments fail a tool's own validation before any outbound call or storage write — e.g. `research_arxiv_search`'s `max_results`/cumulative bounds below, an unsupported `format` passed to `research_resolve_identifier`, a `research_localfile_fetch_full_text` `content_base64` that isn't valid base64 or doesn't sniff as a PDF once decoded, or `page` passed to `research_arxiv_parse_full_text` with `format="html"`), `file_too_large` (`FileTooLargeError` — `research_localfile_fetch_full_text`'s `content_base64` decodes to more than `PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES`, checked both before and after decoding; `research_localfile_upload_chunk`/`research_localfile_finalize_upload` can also raise `not_found`, `invalid_request`, and `file_too_large` for the equivalent per-chunk and reassembled-total cases — see [Local filesystem](#local-filesystem) below), `rate_limited` (`RateLimitedError` — the provider's outbound queue exhausted its bounded backoff after repeated 429s from the source — see [Non-functional requirements → Rate-limit breaches](04-non-functional-requirements.md#rate-limit-breaches-are-handled-inside-the-providers-queue-not-by-the-caller); any tool calling out to arXiv or Europe PMC can raise this, not just one specific tool), `provider_unavailable` (`ProviderUnavailableError` — a timeout, connection failure, or 5xx from the source — surfaced immediately, with no retry, unlike `rate_limited` — see [Non-functional requirements → `provider_unavailable` failures are not retried](04-non-functional-requirements.md#provider_unavailable-failures-are-not-retried)).

**Output shapes.** Every successful tool/resource output below is a typed Pydantic model from `src/prioris_mcp/models/` (`models/arxiv.py`, `models/europepmc.py`, `models/localfile.py`, `models/common.py`), not an ad hoc dict. FastMCP serialises a tool's returned model into the MCP response's structured content automatically; the two resource templates that return model data (`.../markdown`, `research://arxiv/categories`) instead JSON-serialise it themselves (`model_dump_json()`) before returning, since FastMCP's resource templates — unlike its tools — don't auto-serialise a returned `BaseModel`. Every model sets `model_config = ConfigDict(extra="forbid")`; a field named `format` (a Python builtin, and this page's own field name throughout) is always declared on the model as `format_` with `alias="format"`, so the wire-level field is still `format`. `research_resolve_identifier`'s output type is a `Union` of two models rather than one (see [`research_resolve_identifier`](#research_resolve_identifier) below) — FastMCP wraps a non-object output schema like this one under a top-level `"result"` key in structured content, unlike every other tool on this page, whose single-model output appears unwrapped.

**Resource URIs.** Both `fetch_full_text` and `parse_full_text` return the resource URI templates from [Functional requirements → Resources](03-functional-requirements.md#resources), instantiated for that call, e.g. `research://arxiv/2106.09685v2/pdf/fulltext`, `research://europepmc/MED:26551875/xml/markdown`, or `research://localfile/20260729-1430-a3f2/pdf/fulltext`.

**Pagination.** `parse_full_text` and the `.../markdown` resource template both accept `offset`/`limit` (integers, optional) and return one bounded page of Markdown rather than the whole string — see [Non-functional requirements → Inline text is paginated, not returned whole](04-non-functional-requirements.md#inline-text-is-paginated-not-returned-whole). Every `parse_full_text` output below includes `offset`, `limit`, `total_length`, and `has_more` alongside `markdown`, even though the per-tool sections list only `markdown`/`resource_uri` for brevity. `research_arxiv_parse_full_text` (PDF only) and `research_localfile_parse_full_text` (always PDF) additionally accept an optional, 1-indexed `page`, and their output additionally includes `total_pages`/`page_range` — always present in the output schema, `null` for a call that isn't page-aware (e.g. `research_arxiv_parse_full_text` with `format="html"`), since FastMCP derives one static schema per tool and can't conditionally omit a field per call — see [Architecture → `parse_full_text`](01-architecture.md#parse_full_text) and [Storage → Per-document structure](02-storage.md#per-document-structure-manifestsqlite-replaces-structurejsonl).

## arXiv

All arXiv tools call `https://export.arxiv.org/api/query`, which returns an Atom 1.0 feed. This is the same endpoint for search, listing, and single-item lookup — they differ only in which query parameters are set. This must be called over `https`, not `http`: `export.arxiv.org` 301-redirects every plain-`http` request to `https`, so calling `http` directly would cost an extra round-trip on every single arXiv API call for no benefit.

### arXiv metadata record shape

Every arXiv tool that returns article data (`search`, `list_top_n`, `fetch_metadata`) returns this same shape, one per result, taken directly from one Atom `<entry>`:

| Field | Source | Notes |
|---|---|---|
| `arxiv_id` | `<id>` | Always version-suffixed (e.g. `2106.09685v2`) — this is arXiv's own canonicalisation, not something PriorisMCP computes. |
| `title` | `<title>` | |
| `authors` | `<author>` (repeating) | List of `{name, affiliation}`; `affiliation` is optional (`<arxiv:affiliation>`). |
| `abstract` | `<summary>` | |
| `categories` | `<category>` (repeating) | List of category codes (e.g. `cs.CL`). |
| `primary_category` | `<arxiv:primary_category>` | |
| `published` | `<published>` | ISO 8601; version 1's submission date. |
| `updated` | `<updated>` | ISO 8601; current version's submission date. |
| `pdf_url` | `<link rel="related" type="application/pdf">` | Taken directly from the feed, not hand-built — arXiv already gives the exact PDF URL (e.g. `https://arxiv.org/pdf/2106.09685v2`). |
| `doi` | `<arxiv:doi>` | Optional — present only if the paper has a DOI. |
| `journal_ref` | `<arxiv:journal_ref>` | Optional. |
| `comment` | `<arxiv:comment>` | Optional. |

### `research_arxiv_search`

**Input:** `query` (string, required — arXiv `search_query` syntax: field prefixes `ti`/`au`/`abs`/`co`/`jr`/`cat`/`rn`/`all`, boolean `AND`/`OR`/`ANDNOT`), `max_results` (integer, optional, default 10), `start` (integer, optional, default 0), `sort_by` (`relevance` \| `lastUpdatedDate` \| `submittedDate`, default `relevance`), `sort_order` (`ascending` \| `descending`, default `descending`).

**Underlying call:** `GET export.arxiv.org/api/query?search_query={query}&start={start}&max_results={max_results}&sortBy={sort_by}&sortOrder={sort_order}`.

**Output:** `{"results": [<arXiv metadata record>, ...], "total_results": <int>}` — `total_results` from `<opensearch:totalResults>`.

**Bounds:** arXiv itself caps `max_results` at 2000 per request and `start + max_results` at 30000 cumulative; a request exceeding either gets HTTP 400 from arXiv. `research_arxiv_search` must validate against these bounds itself and return a clear error rather than passing through arXiv's raw 400.

### `research_arxiv_list_top_n`

**Input:** `include_categories` (list of strings, required, one or more — arXiv subject classes, e.g. `["cs.CL", "cs.LG"]`), `n` (integer, required — result count), `exclude_categories` (list of strings, optional — arXiv subject classes to exclude).

**Underlying call:** `GET export.arxiv.org/api/query?search_query={query}&start=0&max_results={n}&sortBy=submittedDate&sortOrder=descending`, where `{query}` is `cat:{i1} AND cat:{i2} ... ANDNOT cat:{e1} ANDNOT cat:{e2} ...` — `include_categories` entries `AND`-joined, followed by an `ANDNOT cat:{e}` clause per `exclude_categories` entry (omitted entirely if `exclude_categories` is empty/absent). Both lists are deduplicated (order preserved) before building the query. "Top N" is defined as the N most recently submitted items matching that query — arXiv has no other notion of ranking within a category.

**Output:** `{"results": [<arXiv metadata record>, ...], "total_results": <int>}`, up to `n` entries — the same `ArxivSearchResult` model `research_arxiv_search` uses (see [Conventions → Output shapes](#conventions) above), reused here rather than a separate results-only model. `total_results` here is simply `len(results)` for this one call, not a true across-query total the way `research_arxiv_search`'s `total_results` is (arXiv's own `<opensearch:totalResults>`) — `list_top_n` has no separate pagination/cursor concept, so the two fields happen to coincide in this response even though they mean different things.

**Validation:** an empty `include_categories` fails with `invalid_request` before any outbound call.

### arXiv category-list resource

**Resource URI:** `research://arxiv/categories` (static, no path parameters).

**Underlying call:** `GET https://oaipmh.arxiv.org/oai?verb=ListSets` — arXiv's OAI-PMH `ListSets` verb, called directly at `oaipmh.arxiv.org` rather than `export.arxiv.org/oai2` (which 301-redirects there, same avoid-the-redirect rationale as `export.arxiv.org/api/query` above). Returns all categories in one response; confirmed live (183 entries) that it has no `resumptionToken`, so no pagination handling is needed.

**Behaviour:** a `setSpec` in the OAI-PMH response (e.g. `physics:astro-ph:CO`) is only included if no other `setSpec` extends it with one more `:segment` — non-leaf entries (e.g. `physics`, `physics:physics`) are archive/group nodes, not real `cat:` values, and are excluded. A leaf's category `code` is derived by dropping the outermost segment and joining the rest with `.` (`physics:astro-ph:CO` → `astro-ph.CO`; `physics:hep-th`, a 2-segment leaf, → `hep-th`). `name` is the `setName` verbatim.

**Output:** `{"categories": [{"code": <string>, "name": <string>}, ...]}`, sorted by `code`.

**Caching:** covered by the standard `ResponseCachingMiddleware` `read_resource` path (`PRIORIS_MCP_RESPONSE_CACHE_TTL`), same as any other resource — no separate persistent cache.

### `research_arxiv_fetch_metadata`

**Input:** `arxiv_ids` (list of strings, required, one or more — version suffix optional per ID).

**Underlying call:** `GET export.arxiv.org/api/query?id_list={arxiv_ids joined by commas}` — arXiv's `id_list` natively accepts a comma-delimited list, so a batch of N identifiers costs one rate-limited request, not N.

**Output:** `{"results": [<arXiv metadata record>, ...], "not_found": [<arxiv_id>, ...]}`. arXiv silently omits unrecognised IDs from the returned feed rather than erroring (confirmed for the single-ID case: an unrecognised ID gets an empty feed, not an HTTP error) — `not_found` is computed as the set difference between requested `arxiv_ids` and the IDs actually present in the response, not from any explicit per-ID error arXiv returns. Exact behaviour for a *partially*-invalid batch (some valid IDs, some not, in one `id_list`) should be confirmed against a live multi-ID request during implementation, since the set-difference approach doesn't depend on which specific failure shape arXiv uses.

### `research_arxiv_fetch_full_text`

**Input:** `arxiv_id` (string, required), `format` (`pdf` \| `html`, required).

**Behaviour:** internally resolves `arxiv_id` to its current canonical (version-pinned) form via `fetch_metadata` first — an unversioned ID always means "whatever is latest" (see [Architecture → `resolve_identifier`](01-architecture.md#resolve_identifier)). Then:

- `pdf`: uses `pdf_url` from the metadata record directly (`https://arxiv.org/pdf/{id}v{n}`) — always available, since arXiv accepts a PDF for every submission.
- `html`: builds `https://arxiv.org/html/{id}v{n}` — **not guaranteed to exist**. arXiv's native HTML rendering is a comparatively recent rollout and isn't retroactively available for every older paper. A 404 here must surface as `format_unavailable`, not the generic `not_found` used for an unrecognised `arxiv_id` — the item exists, just not in this format.

**Output:** `{"location": <reference>, "format": "pdf"|"html", "size_bytes": <int>, "served_from_storage": <bool>, "resource_uri": "research://arxiv/{id}/{format}/fulltext"}`.

### `research_arxiv_parse_full_text`

**Input:** `arxiv_id` (string, required), `format` (`pdf` \| `html`, required — the already-persisted source format to parse), `offset` (integer, optional, default 0), `limit` (integer, optional, default `PRIORIS_MCP_MAX_INLINE_CHARS`), `page` (integer, optional, 1-indexed — **`pdf` only**).

**Behaviour:** when `page` is given, it's resolved against the [per-document manifest](02-storage.md#per-document-structure-manifestsqlite-replaces-structurejsonl) to that page's starting offset, and `offset` becomes relative to it (default 0) rather than to the document. Passing `page` with `format="html"` fails with `invalid_request` — HTML has no page concept to resolve against.

**Output:** `{"markdown": <string>, "offset": <int>, "limit": <int>, "total_length": <int>, "has_more": <bool>, "total_pages": <int | null>, "page_range": <[int, int] | null — the page(s) the returned slice spans>, "resource_uri": "research://arxiv/{id}/{format}/markdown"}`. `total_pages`/`page_range` are always present in the output schema (FastMCP derives one static schema per tool from its Python return type, so a field can't be conditionally omitted per call) but are `null` for `format="html"`, which has no page concept to resolve against. Returns `not_found` if that `(arxiv_id, format)` hasn't been fetched (see [Architecture → `parse_full_text`](01-architecture.md#parse_full_text) — never triggers a fetch itself).

### Rate limiting

Every arXiv tool above must go through the same serialised, per-provider queue described in [Non-functional requirements → Rate limiting](04-non-functional-requirements.md#rate-limiting-must-serialise-not-just-gate), honouring arXiv's Terms of Use requirement of **one request per 3 seconds on a single connection** — this is a hard requirement per arXiv's ToU ("must not attempt to overcome these limits"), not a soft guideline. A 429 from arXiv is handled by the queue's own adaptive backoff, not surfaced per-call, up to the bounded retry limit described in [Non-functional requirements → Rate-limit breaches](04-non-functional-requirements.md#rate-limit-breaches-are-handled-inside-the-providers-queue-not-by-the-caller); only exhausting that bound surfaces `rate_limited` to the caller. A timeout, connection failure, or 5xx from arXiv surfaces immediately as `provider_unavailable` instead, with no retry.

## Europe PMC

Europe PMC tools call `https://www.ebi.ac.uk/europepmc/webservices/rest/`.

### Europe PMC metadata record shape

Taken from a `resultType=core` search/lookup response:

| Field | Source | Notes |
|---|---|---|
| `identifier` | `source` + `id` | Rendered as `{source}:{id}` (e.g. `MED:26551875`). |
| `pmid` | `pmid` | Optional (not every record is MEDLINE-indexed). |
| `pmcid` | `pmcid` | Optional. |
| `doi` | `doi` | Optional. |
| `title` | `title` | |
| `authors` | `authorList` | List of `{full_name, first_name, last_name, initials}`. |
| `abstract` | `abstractText` | Optional — not every record has one. |
| `journal` | `journalInfo` | Nested journal title/issue/publication-date fields. |
| `pub_year` | `pubYear` | |
| `is_open_access` | `isOpenAccess` | `"Y"`/`"N"`. |
| `license` | `license` | Optional — present when Europe PMC has recorded a licence for the item. |
| `full_text_available` | derived from `inEPMC` | `true` only when Europe PMC hosts the full text itself (`inEPMC == "Y"`) — this is exactly the condition `fetch_full_text` below depends on. |

### `research_europepmc_search`

**Input:** `query` (string, required — Europe PMC query syntax, e.g. `field:value AND field:value`), `page_size` (integer, optional, default 25), `cursor_mark` (string, optional, default `*` — Europe PMC's own opaque pagination token, not an offset).

**Underlying call:** `GET .../search?query={query}&format=json&resultType=core&pageSize={page_size}&cursorMark={cursor_mark}`.

**Output:** `{"results": [<Europe PMC metadata record>, ...], "hit_count": <int>, "next_cursor_mark": <string, present only when more results exist>}`. A caller pages by re-invoking with `cursor_mark` set to the previous response's `next_cursor_mark`; Europe PMC's cursor is opaque and must be passed through verbatim, not constructed.

### `research_europepmc_fetch_metadata`

**Input:** `identifiers` (list of strings, required, one or more — each `{source}:{id}` or a bare PMCID).

**Underlying call:** Europe PMC has no dedicated batch parameter like arXiv's `id_list`; instead, each identifier becomes an `(EXT_ID:{id} AND SRC:{source})` clause, `OR`-ed together in one `query` — e.g. two identifiers become `query=(EXT_ID:x AND SRC:MED) OR (EXT_ID:y AND SRC:PMC)&format=json&resultType=core`. Confirmed live for a same-source batch (`(EXT_ID:26551875 OR EXT_ID:30855917) AND SRC:MED` correctly returned both records in one call); the parenthesised per-identifier form for a *mixed*-source batch follows the same query syntax but hasn't been separately live-tested — worth confirming during implementation.

**Output:** `{"results": [<Europe PMC metadata record>, ...], "not_found": [<identifier>, ...]}`, with `not_found` computed the same way as arXiv's — the set difference between requested `identifiers` and the identifiers actually present in `results`.

### `research_europepmc_fetch_full_text`

**Input:** `identifier` (string, required). **No `format` parameter** — unlike arXiv, Europe PMC only ever has one directly-servable full-text format (see below), so exposing a `format` field with exactly one valid value would be schema noise, not a real choice for the caller to make.

**This is deliberately narrower than arXiv's `pdf`/`html`.** Europe PMC's own REST API directly and reliably serves full text in exactly one format — JATS XML, via a dedicated same-domain endpoint — and only for the subset of records it hosts itself. Its `fullTextUrlList` field does list HTML/PDF options, but those routinely point to third-party domains (publisher sites, NCBI Bookshelf, ...) outside `europepmc.org`/`ebi.ac.uk` — the same unvetted-external-domain shape already rejected for DOI routing in [Security → Untrusted identifiers](05-security.md#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests). `research_europepmc_fetch_full_text` therefore only fetches XML, and only when `full_text_available` (`inEPMC == "Y"`) is true.

**Underlying call:** `GET .../{pmcid}/fullTextXML` (requires the PMCID form specifically, e.g. `PMC4767193` — confirmed to return a JATS `<article>` document with `<front>`/`<body>`/`<back>`). `fetch_metadata` is always used first — even when `identifier` is already a bare PMCID — because the PMCID alone doesn't say whether Europe PMC hosts full text for it; the resulting `full_text_available` flag is what this tool actually gates on. If the resolved record has no `pmcid` or `full_text_available` is false, this fails with `format_unavailable` rather than attempting any of the third-party links in `fullTextUrlList`.

**Output:** `{"location": <reference>, "format": "xml", "size_bytes": <int>, "served_from_storage": <bool>, "resource_uri": "research://europepmc/{identifier}/xml/fulltext"}`.

### `research_europepmc_parse_full_text`

**Input:** `identifier` (string, required), `offset` (integer, optional, default 0), `limit` (integer, optional, default `PRIORIS_MCP_MAX_INLINE_CHARS`). **No `format` parameter**, for the same reason as `fetch_full_text` above — the persisted source is always XML, so there's nothing for the caller to choose.

**Output:** `{"markdown": <string>, "offset": <int>, "limit": <int>, "total_length": <int>, "has_more": <bool>, "resource_uri": "research://europepmc/{identifier}/xml/markdown"}`, or `not_found` if `(identifier, "xml")` hasn't been fetched. Converting from JATS XML (a well-structured, semantically-tagged format containing the genuine article body — confirmed live, not just links out to it) is expected to be a materially more reliable Markdown conversion than arXiv's PDF/HTML sources, though this page doesn't specify the conversion mechanism itself (an implementation detail).

### Rate limiting

Europe PMC publishes no numeric rate limit; per [Functional requirements → Europe PMC tools](03-functional-requirements.md#europe-pmc-tools), it self-imposes arXiv's same 1-request-per-3-seconds policy through the same serialised queue described in [Non-functional requirements](04-non-functional-requirements.md), including the same adaptive-backoff-then-`rate_limited` behaviour on a 429, and the same immediate, un-retried `provider_unavailable` on a timeout, connection failure, or 5xx.

## Local filesystem

**Conventions.** The identifier for this source is the server-assigned **caller-facing identifier** (see [Storage → Caller-facing identifiers](02-storage.md#caller-facing-identifiers-for-sources-without-one)), format `YYYYMMDD-HHmm-XXXX` (minute-resolution timestamp, `-`, 4-character lowercase base-36 random suffix — e.g. `20260729-1430-a3f2`). The caller sends file *content*, base64-encoded, not a server-side path — see [Architecture → Local filesystem source](01-architecture.md#local-filesystem-source) for why: a path is only meaningful if the caller and the server process share a filesystem, which `stdio` transport happens to provide but `streamable-http`/`http` do not.

### `research_localfile_fetch_full_text`

**Input:** `content_base64` (string, required — base64-encoded bytes of a PDF the caller already has, e.g. read locally by the MCP client and encoded before the call), `filename` (string, optional — stored as the catalogue entry's `original_identifier` for the caller's own reference; never used to resolve a server-side path).

**Behaviour:**

1. Reject a `content_base64` whose *encoded* length exceeds `4 * ceil(PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES / 3)` with `file_too_large`, before decoding anything — base64's fixed per-byte overhead makes this bound computable from the encoded length alone (default 10MB — see [Configuration](../02-configuration.md)).
2. Decode `content_base64`; fail with `invalid_request` if it isn't valid base64.
3. Re-check the *decoded* length against `PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES` and fail with `file_too_large` if it's still over (the encoded-length check in step 1 only bounds the worst case to the nearest multiple of 3 bytes, so this catches the remaining gap).
4. Sniff the decoded content to confirm it is a PDF (e.g. the `%PDF-` magic prefix); fail with `invalid_request` if it is not, regardless of `filename`'s extension.
5. Compute the SHA-256 hash of the decoded bytes. Check whether `(provider="localfile", content_hash, format="pdf")` already exists in storage (see [Storage → Content-hash canonicalisation](02-storage.md#content-hash-canonicalisation-for-the-local-filesystem-source)):
      - If it exists, reuse the caller-facing identifier already on record for that hash; skip the `write`.
      - If not, mint a new caller-facing identifier (retrying on the rare catalogue collision — see [Storage → Caller-facing identifiers](02-storage.md#caller-facing-identifiers-for-sources-without-one)), `write` the content, and record the catalogue entry (caller-facing ID, content hash, format, `filename` if given, fetch timestamp, size).

**Output:** `{"id": <caller-facing identifier>, "location": <reference>, "format": "pdf", "size_bytes": <int>, "served_from_storage": <bool>, "resource_uri": "research://localfile/{id}/pdf/fulltext"}`.

### `research_localfile_parse_full_text`

**Input:** `id` (string, required — the caller-facing identifier returned by `research_localfile_fetch_full_text`), `offset` (integer, optional, default 0), `limit` (integer, optional, default `PRIORIS_MCP_MAX_INLINE_CHARS`), `page` (integer, optional, 1-indexed — always valid, this source is PDF-only).

**Output:** `{"markdown": <string>, "offset": <int>, "limit": <int>, "total_length": <int>, "has_more": <bool>, "total_pages": <int>, "page_range": <[int, int]> — the page(s) the returned slice spans, "resource_uri": "research://localfile/{id}/pdf/markdown"}`, or `not_found` if `id` isn't in the catalogue (see [Architecture → `parse_full_text`](01-architecture.md#parse_full_text) — never triggers a fetch, and never re-reads the original path).

### Rate limiting

Not applicable — see [Architecture → Local filesystem source](01-architecture.md#local-filesystem-source). Neither tool goes through a per-provider outbound queue.

### `research_localfile_begin_upload`

**Input:** `filename` (string, optional — stored for the session, forwarded to the catalogue entry's `original_identifier` at finalize; never used to resolve a server-side path).

**Output:** `{"session_id": <string>, "max_chunk_bytes": <int>}` — `max_chunk_bytes` echoes `PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CHUNK_BYTES` so the caller can size chunks without needing to know the server's configuration out of band.

**Errors:** `invalid_request` if `PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CONCURRENT_SESSIONS` open sessions already exist (checked after sweeping any TTL-expired sessions).

### `research_localfile_upload_chunk`

**Input:** `session_id` (string, required — from `research_localfile_begin_upload`), `index` (integer, required — zero-based, must equal the number of chunks already accepted for this session), `chunk_base64` (string, required — base64-encoded bytes of this chunk).

**Behaviour:** No `total_chunks` parameter — chunks are required in strict sequential order, and the caller decides when to stop by calling `research_localfile_finalize_upload`.

**Output:** `{"received_index": <int>, "bytes_so_far": <int>}`.

**Errors:** `not_found` if `session_id` is unrecognised or has passed `PRIORIS_MCP_LOCAL_FILE_UPLOAD_SESSION_TTL_SECONDS` since its last chunk; `invalid_request` if `index` isn't exactly the next expected index, or `chunk_base64` isn't valid base64; `file_too_large` if this chunk exceeds `PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CHUNK_BYTES`, or the running total exceeds `PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES`.

### `research_localfile_finalize_upload`

**Input:** `session_id` (string, required).

**Behaviour:** Concatenates all chunks received so far and runs the same magic-byte sniff, size check, content-hash canonicalisation, and persistence as `research_localfile_fetch_full_text` (see above) via a shared internal path. Removes the session whether it succeeds or fails — there is no retry-by-resubmitting-finalize; a caller whose finalize fails must call `research_localfile_begin_upload` again.

**Output:** Same shape as `research_localfile_fetch_full_text`: `{"id": <caller-facing identifier>, "location": <reference>, "format": "pdf", "size_bytes": <int>, "served_from_storage": <bool>, "resource_uri": "research://localfile/{id}/pdf/fulltext"}`.

**Errors:** `not_found` if `session_id` is unrecognised or expired; `invalid_request` if zero chunks were ever uploaded, or the reassembled content doesn't sniff as a PDF; `file_too_large` if the reassembled content exceeds `PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES`.

### Note on `research_localfile_fetch_full_text`

Kept unchanged in behaviour as the small-file path — no forced migration. It remains the simplest option for content well within `PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES` and comfortably under transport size ceilings, but it is a deprecation candidate now that the chunked flow above exists as the recommended alternative for large files, since the two paths otherwise converge on the same validation and persistence logic.

## Storage management

### `research_list_fetched`

**Input:** `provider` (`"arxiv"` \| `"europepmc"` \| `"localfile"`, optional — omitting it lists all providers), `format` (string, optional — further filters within the selected provider(s)).

**Output:** `{"entries": [{"provider": <string>, "identifier": <string>, "format": <string>, "artefact": "document"|"markdown", "fetched_at_or_parsed_at": <ISO 8601 string>, "size_bytes": <int>}, ...]}`, read from `catalogue.sqlite` (see [Storage → The catalogue](02-storage.md#the-catalogue-cataloguesqlite)).

### `research_delete_fetched`

**Input:** `entries` (list of `{"provider": <string>, "identifier": <string>, "format": <string>, "artefact": "document"|"markdown"|"all"}`, required, one or more).

**Behaviour:** removes each matching persisted artefact from `StorageBackend`. `artefact="all"` removes the whole format directory (`document`, `markdown`, any extracted `images/`, `metadata.jsonl`) and that format's rows from the document's shared `manifest.sqlite`; if that was the last format directory for the document, the document-hash directory — including its now-empty `manifest.sqlite` — is removed too (see [Storage → Deletion is per-artefact, not per-format](02-storage.md#deletion-is-per-artefact-not-per-format)). Deleting `artefact="markdown"` or `artefact="all"` also removes any extracted image artefacts and manifest rows anchored to that `markdown`, when [extracted PDF images](#extracted-pdf-images-optional) are enabled. An entry naming a `(provider, identifier, format, artefact)` combination not currently in storage is reported in `not_found`, not treated as a failure of the whole call — the same partial-failure tolerance `research_arxiv_fetch_metadata`/`research_europepmc_fetch_metadata` already have for unrecognised identifiers.

**Does not cascade between artefacts:** deleting `artefact="document"` leaves `markdown` in place, and vice versa — still listed by `research_list_fetched`, still readable, still independently deletable. This preserves the same independent-deletability guarantee the previous flat-format design had (there, expressed as two separately-deletable *formats*, `pdf` and `pdf-markdown`; here as two separately-deletable *artefacts* within one format directory). A caller that wants to fully remove everything stored for a fetch+parse passes `artefact="all"`, or deletes `document` and `markdown` separately.

**Output:** `{"deleted": [<entry>, ...], "not_found": [<entry>, ...]}`, using the same entry shape as the input.

### `research_search_fetched`

**Input:** `query` (string, required — FTS5 query syntax), `provider` (string, optional), `identifier` (string, optional — scopes to one document; requires `provider`), `format` (string, optional).

**Behaviour:** runs `query` against the global FTS5 index via the [`SearchIndex`](01-architecture.md#searchindex) abstraction (see [Storage → Full-text search](02-storage.md#full-text-search-the-searchsqlite3-index)) over persisted chunks — or, for a document with none, its leaves — applying whichever of `provider`/`identifier`/`format` are given as an additional `WHERE` filter alongside `MATCH`. A query can legitimately match both a section and one of its own nested subsections, since every heading-nesting level is indexed as its own row — overlapping matches are expected, not deduplicated. Never triggers a fetch or parse; searching before anything has been persisted simply returns no results, not an error.

**Output:** `{"matches": [{"provider": <string>, "identifier": <string>, "format": <string>, "snippet": <string>, "offset": <int>, "score": <float>}, ...]}`, ranked by FTS5's `bm25()` (most relevant first). `offset` is the matched entry's `span_start` in the document's own coordinate space, not an FTS5-internal offset — a caller can pass it straight to `parse_full_text`'s `offset` parameter to re-fetch surrounding context.

## Extracted PDF images (optional)

Off by default, gated by `PRIORIS_MCP_PDF_EXTRACT_IMAGES` (see [Storage → Future: extracted PDF images](02-storage.md#future-extracted-pdf-images) and [Functional requirements → Extracted PDF images](03-functional-requirements.md#extracted-pdf-images-optional)). When enabled, each extracted image becomes readable as its own MCP resource; the exact URI template is not yet decided — it must not leak filesystem paths (see [Security → Extracted PDF image resources must not leak filesystem paths](05-security.md#extracted-pdf-image-resources-must-not-leak-filesystem-paths)) — and is left open for when this capability is actually implemented, rather than pinned down speculatively here. `research_delete_fetched`'s cascade behaviour for image artefacts is already specified above.

## `research_resolve_identifier`

**Input:** `identifier` (string, required — an arXiv ID, a Europe PMC identifier, or a DOI), `format` (string, required — the desired target format; valid values depend on which provider ends up servicing the identifier).

**Output on success:** one of two Pydantic models — `ResolvedIdentifierResult = ArxivResolvedIdentifierResult | EuropePmcResolvedIdentifierResult` in `models/common.py` — returned wrapped under a top-level `"result"` key in structured content (see [Conventions → Output shapes](#conventions) above, since a `Union` isn't itself an object schema). `ArxivResolvedIdentifierResult`: `{"provider": "arxiv", "identifier": <canonical form>, "resolved_url": <string>, "format": <string>}`. `EuropePmcResolvedIdentifierResult`: the same four fields (`provider: "europepmc"`) plus `full_text_available` (bool) — carried through from the Europe PMC provider's own `resolve_identifier` (see [Europe PMC → `research_europepmc_fetch_full_text`](#research_europepmc_fetch_full_text)) — telling the caller upfront whether a subsequent `research_europepmc_fetch_full_text` call for this identifier will succeed. arXiv-routed identifiers carry no such extra field.

**On failure:** raises `UnsupportedProviderError` (the `unsupported_provider` category — see [Conventions → Errors](#conventions) above) per [Architecture → Identifier routing](01-architecture.md#identifier-routing-grouping-level) — a DOI resolving to any domain other than `arxiv.org` or `europepmc.org`/`ncbi.nlm.nih.gov` fails here rather than attempting to serve it.
