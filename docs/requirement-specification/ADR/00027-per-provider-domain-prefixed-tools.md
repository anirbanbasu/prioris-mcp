---
status: accepted
date: 2026-08-13
---

# ADR-00027: Per-provider, domain-prefixed tools, not one generic tool parameterised by provider

## Context

PriorisMCP exposes `search`, `fetch_metadata`, `fetch_full_text`, and `parse_full_text` (plus `list_top_n`, arXiv-only in v1) for each v1 source — arXiv and Europe PMC. These could be exposed as one tool per capability, scoped to a source by a `provider` argument, or as separate tools per provider.

## Decision

Each capability is exposed as a **per-provider** tool — `research_arxiv_*`, `research_europepmc_*` — not one generic tool parameterised by provider. `list_top_n` follows the same convention (arXiv-only in v1). `resolve_identifier` is the deliberate single exception to this pattern — see [Architecture → Identifier routing](../01-architecture.md#identifier-routing-grouping-level) — since a DOI can't be scoped to one provider before it's resolved.

## Alternatives considered

- **A single generic tool taking a `provider` parameter** (e.g. `research_fetch_metadata(provider, identifier)`) — rejected on schema-tightness grounds: identifier patterns (an arXiv ID vs. a Europe PMC identifier) and valid `format` values genuinely differ per provider. A generic tool would need either a loose, unvalidated identifier field, or a `format` enum whose valid values secretly depend on whatever `provider` value was also passed — neither is expressible cleanly as a JSON schema, and both push validation into runtime code instead of the tool's own contract, which is what an MCP client (an LLM) actually reads to avoid mistakes.

## Consequences

The `research_` prefix isn't there to disambiguate — `arxiv_fetch_metadata` is already unambiguous on its own — it's a scanability convention for a flat MCP tool list, so all research-publication tools sort and group together regardless of source, and won't collide with a future `patent_*` domain's tools of the same shape (e.g. `patent_uspto_fetch_metadata`). This does mean N providers × M capabilities register as individual tools rather than M tools total — accepted as the cost of per-tool schema validation.

## Referenced from

- [Functional requirements → Tool surface: per-provider, domain-prefixed](../03-functional-requirements.md#tool-surface-per-provider-domain-prefixed)
