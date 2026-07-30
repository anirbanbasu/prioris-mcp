---
icon: lucide/wrench
---

# Tools

All research tools are prefixed `research_` and grouped by provider (`research_arxiv_*`, `research_europepmc_*`), with identifier resolution exposed as a single grouping-level tool, `research_resolve_identifier`, that isn't tied to one provider. See the [Software Requirements Specification](requirement-specification/index.md) — specifically [Architecture](requirement-specification/01-architecture.md) and [Interface specification](requirement-specification/06-interface-specification.md) — for the full design rationale and exact wire-level schemas; this page is a practical, per-tool reference.

## Errors

Every tool below returns a common envelope on failure: `{"error": "<code>", "message": "<detail>"}`. Codes used across all tools:

| Code | Meaning |
|---|---|
| `not_found` | Identifier not recognised by the provider, or the requested format hasn't been fetched yet. |
| `format_unavailable` | The identifier is valid, but doesn't offer the requested format. |
| `unsupported_provider` | A DOI resolved to a domain outside the v1 provider allowlist (arXiv, Europe PMC). |
| `invalid_request` | Caller-supplied arguments fail validation before any outbound call (e.g. an arXiv search exceeding arXiv's own result-count bounds, or an unsupported `format` passed to `research_resolve_identifier`). |
| `rate_limited` | The provider's outbound queue exhausted its backoff budget after repeated `429`s from the source. |
| `provider_unavailable` | A timeout, connection failure, or `5xx` from the source — surfaced immediately, never retried. |
| `file_too_large` | `research_localfile_fetch_full_text`'s path resolves to a file exceeding `PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES`. |

## arXiv tools

| Tool | Description | Key inputs | Notes |
|---|---|---|---|
| `research_arxiv_search` | Search arXiv by keyword/query. | `query`, `max_results` (default 10), `start` (default 0), `sort_by`, `sort_order` | Validates `max_results` (≤2000) and `start + max_results` (≤30000) before calling arXiv. |
| `research_arxiv_list_top_n` | List the *N* most recently submitted items matching one or more arXiv subject categories. | `include_categories` (list, e.g. `["cs.CL"]`), `n`, `exclude_categories` (list, optional) | "Top N" means most recent, not most cited/viewed. `include_categories` is `AND`-combined, `exclude_categories` is `ANDNOT`-combined; both are deduplicated. Empty `include_categories` fails with `invalid_request`. |
| `research_arxiv_fetch_metadata` | Fetch metadata for one or more arXiv identifiers in a single call. | `arxiv_ids` (list) | Unrecognised IDs are reported in `not_found`, not a failure. |
| `research_arxiv_fetch_full_text` | Fetch (or return the already-persisted) full text for an arXiv item. | `arxiv_id`, `format` (`pdf`\|`html`) | Unversioned IDs resolve to the current version first. `html` isn't available for every paper (arXiv's HTML rendering is a comparatively recent rollout). |
| `research_arxiv_parse_full_text` | Convert already-fetched arXiv full text into one page of Markdown. | `arxiv_id`, `format`, `offset` (default 0), `limit` (default `PRIORIS_MCP_MAX_INLINE_CHARS`) | Never triggers a fetch itself — fetch first, or this fails with `not_found`. Returns `offset`/`limit`/`total_length`/`has_more` alongside `markdown` so a caller can page through content longer than the limit; see [Caching and rate limiting](#caching-and-rate-limiting) below. |

All arXiv tools share a single outbound request queue, serialised to arXiv's documented limit of one request per 3 seconds.

## Europe PMC tools

| Tool | Description | Key inputs | Notes |
|---|---|---|---|
| `research_europepmc_search` | Search Europe PMC by keyword/query. | `query`, `page_size` (default 25), `cursor_mark` (default `*`) | Paginate by passing the previous response's `next_cursor_mark` back in as `cursor_mark`. |
| `research_europepmc_fetch_metadata` | Fetch metadata for one or more Europe PMC identifiers in a single call. | `identifiers` (list — bare PMCID or `{source}:{id}`) | Unrecognised identifiers are reported in `not_found`, not a failure. |
| `research_europepmc_fetch_full_text` | Fetch (or return the already-persisted) JATS XML full text for a Europe PMC item. | `identifier` | No `format` parameter — Europe PMC's only directly-servable full-text format is XML. Fails with `format_unavailable` if Europe PMC doesn't host full text for that item itself. |
| `research_europepmc_parse_full_text` | Convert already-fetched Europe PMC XML full text into one page of Markdown. | `identifier`, `offset` (default 0), `limit` (default `PRIORIS_MCP_MAX_INLINE_CHARS`) | Never triggers a fetch itself — fetch first, or this fails with `not_found`. Returns `offset`/`limit`/`total_length`/`has_more` alongside `markdown`, same as arXiv's. |

There is no `research_europepmc_list_top_n` — Europe PMC has no single classification field equivalent to arXiv's subject categories.

Europe PMC publishes no numeric rate limit; the provider self-imposes the same one-request-per-3-seconds policy as arXiv, through its own separate queue.

## Identifier resolution

| Tool | Description | Key inputs | Notes |
|---|---|---|---|
| `research_resolve_identifier` | Resolve an identifier of unknown provider — an arXiv ID, a Europe PMC identifier, or a DOI — to its owning provider, canonical identifier, and a fetchable URL. | `identifier`, `format` | Self-identifying schemes (arXiv IDs, Europe PMC identifiers) route directly, with no network round-trip. A DOI resolves via `doi.org`/Crossref first; if the redirect lands outside the arXiv/Europe PMC domain allowlist, this fails with `unsupported_provider` rather than following it. |

This is the one capability exposed at the grouping level rather than per-provider — see [Architecture → Identifier routing](requirement-specification/01-architecture.md#identifier-routing-grouping-level).

## Local filesystem tools

| Tool | Description | Key inputs | Notes |
|---|---|---|---|
| `research_localfile_fetch_full_text` | Validate and persist caller-sent PDF bytes. | `content_base64`, `filename` (optional) | Rejects invalid base64, or a payload whose (encoded or decoded) size exceeds `PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES`, with `file_too_large`/`invalid_request`. Validates content as a PDF by its magic bytes, not `filename`'s extension. Re-fetching unchanged content reuses the same server-assigned `id`; changed content gets a new one. |
| `research_localfile_parse_full_text` | Convert an already-fetched local PDF's full text into one page of Markdown. | `id`, `offset` (default 0), `limit` (default `PRIORIS_MCP_MAX_INLINE_CHARS`) | `id` is the caller-facing identifier `research_localfile_fetch_full_text` returned. Never triggers a fetch — fails with `not_found` if `id` isn't recognised. |

Deliberately narrower than the arXiv/Europe PMC tool sets — no search, listing, metadata, or identifier resolution for this source (see [Architecture → Local filesystem source](requirement-specification/01-architecture.md#local-filesystem-source)). Neither tool is subject to rate limiting — there's no outbound network request to throttle.

## Storage management tools

| Tool | Description | Key inputs | Notes |
|---|---|---|---|
| `research_list_fetched` | Enumerate persisted `(provider, identifier, format)` manifest entries. | `provider` (optional), `format` (optional) | Never triggers a fetch or parse. Omitting both filters lists every persisted entry across all three sources. |
| `research_delete_fetched` | Remove one or more persisted entries. | `entries` (list of `{provider, identifier, format}`) | Tolerates entries no longer present — reports them in `not_found` rather than failing the whole call. Only removes the storage entry itself; never touches a local filesystem source's original file. |

Grouping-level, like `research_resolve_identifier` — not split per provider, since neither tool validates anything provider-specific (see [Architecture → `list_fetched`/`delete_fetched`](requirement-specification/01-architecture.md#list_fetched-delete_fetched-grouping-level)).

## Caching and rate limiting

`research_*_search`, `research_*_list_top_n`, and `research_*_fetch_metadata` responses are covered by the server's response-caching middleware (`PRIORIS_MCP_RESPONSE_CACHE_TTL`, see [Configuration](02-configuration.md)). `fetch_full_text` and `parse_full_text` are backed by persistent storage instead (see [Resources](04-resources.md)) — a repeat call returns the already-persisted content (`served_from_storage: true`) without a second network fetch or parse.

Response shapes are still evolving alongside the SRS — prefer the [Interface specification](requirement-specification/06-interface-specification.md) as the source of truth for exact wire-level fields.

`research_localfile_fetch_full_text`/`research_localfile_parse_full_text` and `research_list_fetched`/`research_delete_fetched` are excluded from the response cache entirely — the local file source re-hashes file content on every call by design, and list/delete must reflect live storage state.
