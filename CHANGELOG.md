# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/) and this project adheres to [Semantic Versioning](https://semver.org/).

## [unreleased]

### Added

- Chunked upload for the local filesystem source: `research_localfile_begin_upload`, `research_localfile_upload_chunk`, `research_localfile_finalize_upload` (fix [issue #17](https://github.com/anirbanbasu/prioris-mcp/issues/17)).
- `src/prioris_mcp/models/` (`arxiv`, `europepmc`, `localfile`, `common`): typed, per-shape Pydantic output models for every `research_*` tool and resource, replacing the previous ad hoc `dict` returns across the arXiv/Europe PMC/local filesystem providers and identifier routing.
- Explicit, airgapped-friendly OCR configuration for `LiteParsePdfBackend`: `PRIORIS_MCP_PDF_OCR_ENABLED`, `PRIORIS_MCP_PDF_OCR_TESSDATA_PATH` (falls back to `TESSDATA_PREFIX`), `PRIORIS_MCP_PDF_OCR_SERVER_URL`, and `PRIORIS_MCP_PDF_OCR_SERVER_HEADERS`, instead of relying on liteparse's own defaults (which lazily download Tesseract language data over the network) — see [Configuration](docs/02-configuration.md) and [Security](docs/requirement-specification/05-security.md#ocr-language-data-is-a-network-dependency-of-parse_full_text) (fix [issue #11](https://github.com/anirbanbasu/prioris-mcp/issues/11)).

### Changed

- **Breaking:** `research_localfile_fetch_full_text` now accepts `content_base64` (base64-encoded PDF bytes) and an optional `filename` hint instead of a server-side `path`, so "local" means local to the MCP client consistently across transports rather than local to wherever the server process happens to run (addresses [issue #6](https://github.com/anirbanbasu/prioris-mcp/issues/6), see "Fixed" section below). The `PRIORIS_MCP_LOCAL_FILE_ROOT` environment variable is removed as a result (no server-side path is resolved anymore); `PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES` still bounds the decoded content size.
- **Breaking:** every tool's business-logic failures (`not_found`, `format_unavailable`, `unsupported_provider`, `invalid_request`, `file_too_large`, `rate_limited`, `provider_unavailable`) no longer come back as the structured `{"error": "<code>", "message": "<detail>"}` envelope. Providers now raise their typed exception directly, and FastMCP surfaces it to the caller as an opaque `ToolError` with a human-readable message only — there is no longer a machine-readable error code in the response.

### Deprecated

- None documented yet.

### Removed

- `errors.to_error_envelope`/`errors.call_returning_envelope` and the internal `_ERROR_CODES` table (zero callers now that providers raise typed exceptions directly instead of being translated into the removed envelope); the exception classes themselves (`NotFoundError`, `FormatUnavailableError`, `UnsupportedProviderError`, `InvalidRequestError`, `FileTooLargeError`) are unchanged.

### Fixed

- Fixed [issue #10](https://github.com/anirbanbasu/prioris-mcp/issues/10) through [PR #14](https://github.com/anirbanbasu/prioris-mcp/pull/14).
- Fixed [issue #9](https://github.com/anirbanbasu/prioris-mcp/issues/9), [issue #8](https://github.com/anirbanbasu/prioris-mcp/issues/8) and [issue #7](https://github.com/anirbanbasu/prioris-mcp/issues/7) through [PR #15](https://github.com/anirbanbasu/prioris-mcp/pull/15).
- Fixed [issue #6](https://github.com/anirbanbasu/prioris-mcp/issues/6) through [PR #16](https://github.com/anirbanbasu/prioris-mcp/pull/16).

### Security

- None documented yet.

## [0.1.0.post2] - 2026-07-28

### Added

- `research_arxiv_parse_full_text` and `research_europepmc_parse_full_text` now accept `offset`/`limit` and return one bounded page of Markdown (`offset`, `limit`, `total_length`, `has_more`) instead of the whole string, so a large parsed PDF/HTML/XML document no longer risks exceeding an MCP client's own max-tokens-per-result limit. The default page size is configurable via a new `PRIORIS_MCP_MAX_INLINE_CHARS` environment variable (default 20000 characters).
- The `research://{provider}/{identifier}/{format}/markdown` resource template now accepts the same `offset`/`limit` query parameters, so previously-parsed content can be paged through via a resource read instead of re-invoking the tool.

### Changed

- Upgraded dependencies.

### Deprecated

- None documented yet.

### Removed

- None documented yet.

### Fixed

- `provider_unavailable` errors from a transport-level failure (timeout, connection error) now name the underlying exception type in the error message, instead of sometimes rendering as `"... failed: "` with nothing after the colon.
- The outbound HTTP client's timeout is now configurable (`PRIORIS_MCP_HTTP_TIMEOUT_SECONDS`, default 30s) instead of httpx's own 5-second default, which was too tight for arXiv/Europe PMC under load and a likely cause of spurious `provider_unavailable` failures.
- arXiv API calls now go to `https://export.arxiv.org/api/query` directly instead of `http://`, avoiding an extra redirect round-trip (`export.arxiv.org` 301-redirects every plain-`http` request to `https`) on every single arXiv API call.

### Security

- None documented yet.

## [0.1.0] - 2026-07-28

### Added

- The `research_arxiv_search`, `research_arxiv_list_top_n`, `research_arxiv_fetch_metadata`, `research_arxiv_fetch_full_text`, and `research_arxiv_parse_full_text` tools for the arXiv provider.
- The `research_europepmc_search`, `research_europepmc_fetch_metadata`, `research_europepmc_fetch_full_text`, and `research_europepmc_parse_full_text` tools for the Europe PMC provider.
- The `research_resolve_identifier` tool, resolving an arXiv ID, a Europe PMC identifier, or a DOI to its owning provider and resolved URL.
- Two MCP resource templates, `research://{provider}/{identifier}/{format}/fulltext` and `research://{provider}/{identifier}/{format}/markdown`, for reading back already-fetched or already-parsed content.
- A filesystem-backed storage layer with in-flight de-duplication, so concurrent fetch/parse calls for the same identifier never race or duplicate work.
- A pluggable parser-backend interface, with PDF (`LiteParsePdfBackend`), HTML (`MarkdownifyHtmlBackend`), and JATS XML (`JatsXsltMarkdownBackend`, via a vendored NCBI JATS-to-HTML XSLT stylesheet) implementations, each converting fetched full text to Markdown.
- A per-provider rate-limit/backoff request queue with a configurable total backoff budget (`PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS`).
- A shared, typed error envelope (`{"error": "<code>", ...}`) returned by every tool for business-logic failures, with codes `not_found`, `format_unavailable`, `unsupported_provider`, `invalid_request`, `rate_limited`, and `provider_unavailable`.
- Response caching middleware for tool, resource, and prompt list/read/call responses.
- New configuration: `PRIORIS_MCP_STORAGE_DIR`, `PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS`, and `PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS`.
- The project's Software Requirement Specification, published at [docs-prioris-mcp.anirbanbasu.com](https://docs-prioris-mcp.anirbanbasu.com).

### Changed

- The default `PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS` is now restricted to `http://localhost`/`http://127.0.0.1` instead of a wildcard (`*`).

### Removed

- The `greet` placeholder tool, scaffolding from the initial project setup.

### Security

- Fetched JATS XML is parsed with protection against entity-expansion ("billion laughs") attacks and a bounded parse timeout.
- Concurrently *executing* (not just concurrently *awaited*) JATS-to-HTML transforms are capped by `PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS` (default `min(4, os.cpu_count())`), closing an unbounded-thread-accumulation risk under sustained malicious input.
- The Europe PMC provider only ever follows its own same-domain `fullTextXML` endpoint for full-text retrieval, never the publisher- or third-party-hosted full-text URLs surfaced in search results.

[unreleased]: https://github.com/anirbanbasu/prioris-mcp/compare/v0.1.0.post2...HEAD
[0.1.0.post2]: https://github.com/anirbanbasu/prioris-mcp/compare/v0.1.0...v0.1.0.post2
[0.1.0]: https://github.com/anirbanbasu/prioris-mcp/compare/v0.0.1...v0.1.0
