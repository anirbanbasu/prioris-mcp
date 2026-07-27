# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/) and this project adheres to [Semantic Versioning](https://semver.org/).

## [unreleased]

### Added

- None documented yet.

### Changed

- None documented yet.

### Deprecated

- None documented yet.

### Removed

- None documented yet.

### Fixed

- None documented yet.

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

[unreleased]: https://github.com/anirbanbasu/prioris-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/anirbanbasu/prioris-mcp/compare/v0.0.1...v0.1.0
