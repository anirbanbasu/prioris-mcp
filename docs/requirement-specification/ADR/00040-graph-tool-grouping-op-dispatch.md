---
status: accepted
date: 2026-09-20
---

# ADR-00040: Tool grouping: three op-dispatched tools, not one tool per operation or one mega-tool

## Context

The graph interface spans 19 distinct operations (4 node writes, 3 edge writes, 5 reads/traversal, 7 algorithms). This codebase's established convention is one tool per verb (e.g. `research_notes_create`/`_read`/`_update`/`_delete` as four separate tools) rather than dispatch-by-parameter bundling; the one existing dispatch-by-parameter precedent, `mode` on `research_search_fetched`/`research_notes_search` ([ADR-00024](00024-search-composition-mode-no-server-fusion.md)), is narrowly scoped to selecting among search mechanisms that already share one request/response shape.

## Decision

Three tools, each dispatched by an `op: Literal[...]` parameter, grouped by operation category rather than by individual operation: `research_graph_write` (all seven node/edge write operations), `research_graph_query` (the five read/traversal operations), `research_graph_analyze` (the seven algorithm operations). Each `op`'s parameters are validated at runtime against that op's actual requirements — the same `InvalidRequestError` idiom `research_search_fetched` already uses for cross-field validation — and each tool's return type is a discriminated union keyed on `op`, not a loose untyped envelope.

## Alternatives considered

- **One tool per operation** (19 separate tools) — rejected: far more tools than any other capability in this codebase exposes for one feature area, working against discoverability rather than for it.
- **One tool per verb spanning both node and edge** (e.g. separate `research_graph_create`/`_update`/`_delete`/`_query`/`_analyze`, five tools) — closer to the notes precedent's per-verb split, but doesn't meaningfully reduce complexity over the chosen grouping and fragments write validation (e.g. [ADR-00037](00037-graph-metadata-merge-conflict-policy.md)'s metadata-merge-conflict policy) across more tool bodies than necessary.
- **One single tool for everything** (one `op` enum spanning all 19 operations) — rejected: conflates read-only and destructive operations under one `destructiveHint`/`readOnlyHint` annotation, which MCP clients may use to gate confirmation prompts — collapsing them would either over-warn on safe reads or under-warn on destructive writes.

## Consequences

`research_graph_write` carries `destructiveHint: True` (it includes the delete operations) while `research_graph_query`/`research_graph_analyze` carry `readOnlyHint: True` — annotation-level safety signalling stays accurate per tool. This establishes op-dispatch-by-category as a second, explicit pattern in this codebase alongside "mode" and "one tool per verb" — future features with comparably high operation counts have a precedent to follow rather than inventing a third pattern from scratch.

## Referenced from

- [Graph search → Tool grouping: three op-dispatched tools](../search/03-graph-search.md#tool-grouping-three-op-dispatched-tools-not-one-tool-per-operation-or-one-mega-tool)
