---
status: accepted
date: 2026-09-20
---

# ADR-00041: `research_graph_*` are new tools, not a `mode` on `research_search_fetched`

## Context

`research_search_fetched`'s `mode` parameter already dispatches across multiple retrieval mechanisms (`fts`, `vector`, `hybrid` — [ADR-00024](00024-search-composition-mode-no-server-fusion.md)), all sharing one request shape (a query string) and one response shape (a ranked list of `{provider, identifier, format, chunk_id, offset, snippet, score}` matches). Graph operations could superficially look like a candidate fourth mode.

## Decision

The three graph tools (`research_graph_write`/`_query`/`_analyze`, [ADR-00040](00040-graph-tool-grouping-op-dispatch.md)) are standalone tools, not folded into `research_search_fetched`'s `mode`. This follows the same reasoning [ADR-00016](00016-research-discovery-new-tool-not-mode.md) already established for `research_discovery`: a capability whose result shape is structurally incompatible with `mode`'s uniform matches-list contract gets its own tool rather than forcing that shape.

## Alternatives considered

- **A `mode="graph"` option on `research_search_fetched`** — rejected: graph operations don't share `mode`'s request/response contract at all. `get_node` returns one node, not a ranked list; `neighbors` returns edge+node pairs; `subgraph` returns a whole node/edge set; only `find_concepts` is even query-shaped, and it exists for write-time dedup, not retrieval. Forcing these into `mode` would mean either inventing a lossy common shape or breaking the "same fields, same meaning" guarantee `mode` currently makes across `fts`/`vector`.

## Consequences

A caller wanting both lexical/semantic search and graph traversal issues separate tool calls rather than one composed call — composing them is left to whatever orchestrates tool calls on the client side, not built into the server. `research_search_fetched`'s `mode` stays a closed, uniform-shape contract, unaffected by graph's very different result shapes.

## Referenced from

- [Graph search → Graph tools are standalone, not a `research_search_fetched` mode](../search/03-graph-search.md#research_graph_-are-new-tools-not-a-mode-on-research_search_fetched)
