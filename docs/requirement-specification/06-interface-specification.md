---
icon: lucide/plug
---

# Interface specification

This page states the exact wire-level input/output contract for every v1 MCP tool and resource named in [Functional requirements](03-functional-requirements.md), grounded in the arXiv and Europe PMC APIs as they actually behave — verified live against `export.arxiv.org`, `arxiv.org`, and `www.ebi.ac.uk/europepmc` while drafting this page, not recalled from memory. Where a detail depends on upstream API behaviour, the upstream call it derives from is stated alongside it.

## Conventions

**Identifiers.** An arXiv identifier is a bare or version-suffixed arXiv ID (`2106.09685` or `2106.09685v2`), or the older, pre-2007 category-prefixed form (`hep-th/9901001` or `hep-th/9901001v1`) — both shapes remain valid arXiv identifiers and both are recognised by `research_resolve_identifier`'s routing. A Europe PMC identifier is the composite `{source}:{id}` form Europe PMC itself uses internally (e.g. `MED:26551875`), with a bare PMCID (`PMC4767193`) also accepted as shorthand since it's unambiguous on its own (implicitly `SRC:PMC`).

**Errors.** Every tool returns a common error envelope on failure: `{"error": "<code>", "message": "<human-readable detail>"}`. Error codes used across this page: `not_found` (identifier not recognised by the provider, or requested format not persisted — see [Architecture → `parse_full_text`](01-architecture.md#parse_full_text)), `format_unavailable` (identifier is valid but doesn't offer the requested format — e.g. an older arXiv paper with no native HTML rendering), `unsupported_provider` (see [Architecture → Identifier routing](01-architecture.md#identifier-routing-grouping-level)), `invalid_request` (caller-supplied arguments fail a tool's own validation before any outbound call — e.g. `research_arxiv_search`'s `max_results`/cumulative bounds below, or an unsupported `format` passed to `research_resolve_identifier`), `rate_limited` (the provider's outbound queue exhausted its bounded backoff after repeated 429s from the source — see [Non-functional requirements → Rate-limit breaches](04-non-functional-requirements.md#rate-limit-breaches-are-handled-inside-the-providers-queue-not-by-the-caller); any tool calling out to arXiv or Europe PMC can surface this, not just one specific tool), `provider_unavailable` (a timeout, connection failure, or 5xx from the source — surfaced immediately, with no retry, unlike `rate_limited` — see [Non-functional requirements → `provider_unavailable` failures are not retried](04-non-functional-requirements.md#provider_unavailable-failures-are-not-retried)).

**Resource URIs.** Both `fetch_full_text` and `parse_full_text` return the resource URI templates from [Functional requirements → Resources](03-functional-requirements.md#resources), instantiated for that call, e.g. `research://arxiv/2106.09685v2/pdf/fulltext` or `research://europepmc/MED:26551875/xml/markdown`.

**Pagination.** `parse_full_text` and the `.../markdown` resource template both accept `offset`/`limit` (integers, optional) and return one bounded page of Markdown rather than the whole string — see [Non-functional requirements → Inline text is paginated, not returned whole](04-non-functional-requirements.md#inline-text-is-paginated-not-returned-whole). Every `parse_full_text` output below includes `offset`, `limit`, `total_length`, and `has_more` alongside `markdown`, even though the per-tool sections list only `markdown`/`resource_uri` for brevity.

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

**Input:** `category` (string, required — an arXiv subject class, e.g. `cs.CL`), `n` (integer, required — result count).

**Underlying call:** `GET export.arxiv.org/api/query?search_query=cat:{category}&start=0&max_results={n}&sortBy=submittedDate&sortOrder=descending`. "Top N" is defined as the N most recently submitted items in that category — arXiv has no other notion of ranking within a category.

**Output:** `{"results": [<arXiv metadata record>, ...]}`, up to `n` entries.

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

**Input:** `arxiv_id` (string, required), `format` (`pdf` \| `html`, required — the already-persisted source format to parse), `offset` (integer, optional, default 0), `limit` (integer, optional, default `PRIORIS_MCP_MAX_INLINE_CHARS`).

**Output:** `{"markdown": <string>, "offset": <int>, "limit": <int>, "total_length": <int>, "has_more": <bool>, "resource_uri": "research://arxiv/{id}/{format}/markdown"}`, or `not_found` if that `(arxiv_id, format)` hasn't been fetched (see [Architecture → `parse_full_text`](01-architecture.md#parse_full_text) — never triggers a fetch itself).

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

## `research_resolve_identifier`

**Input:** `identifier` (string, required — an arXiv ID, a Europe PMC identifier, or a DOI), `format` (string, required — the desired target format; valid values depend on which provider ends up servicing the identifier).

**Output on success:** `{"identifier": <canonical form>, "provider": "arxiv"|"europepmc", "resolved_url": <string>, "format": <string>}`, plus a provider-dependent extra field: for a Europe PMC-routed identifier, `full_text_available` (bool) is also present — carried through from the Europe PMC provider's own `resolve_identifier` (see [Europe PMC → `research_europepmc_fetch_full_text`](#research_europepmc_fetch_full_text)) — telling the caller upfront whether a subsequent `research_europepmc_fetch_full_text` call for this identifier will succeed. arXiv-routed identifiers carry no such extra field.

**Output on failure:** `{"error": "unsupported_provider", "message": <detail>}` per [Architecture → Identifier routing](01-architecture.md#identifier-routing-grouping-level) — a DOI resolving to any domain other than `arxiv.org` or `europepmc.org`/`ncbi.nlm.nih.gov` fails here rather than attempting to serve it.
