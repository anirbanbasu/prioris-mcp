---
status: accepted
date: 2026-08-13
---

# ADR-00027: Graph search engine: `graphqlite`, a Cypher-to-SQL transpiler over the same SQLite file

## Context

`GraphSearchBackend` needs a concrete storage/query engine. [Vector search → Interfaces stay separate](../search/02-vector-search.md#interfaces-stay-separate-even-if-a-future-engine-could-serve-more-than-one) had flagged LadybugDB as a candidate, pending the same diligence [ADR-00021](00021-vector-engine-sqlite-vec.md) gave `sqlite-vec`. That diligence surfaced a materially different risk picture than the placeholder assumed.

## Decision

`graphqlite` (`colliery-io`) — MIT-licensed, actively maintained, 97.7% openCypher TCK conformance, built-in graph algorithms. Architecturally a transpiler, not a custom storage engine: Cypher is parsed to an AST and compiled to SQL against its own EAV schema, executed through SQLite's standard APIs. Its Python binding supports `wrap()` on an already-open stdlib `sqlite3.Connection`, extending the same connection pattern `StorageBackend`, `SqliteFts5SearchIndex`, and `sqlite-vec` already use.

## Alternatives considered

- **KuzuDB-derived forks (LadybugDB, Vela-Engineering's `kuzu` fork)** — rejected: KuzuDB itself was archived in October 2025 after Apple acquired the company behind it, with no confirmed continuation path from the original maintainers. LadybugDB and Vela are two competing community forks racing to fill the gap, with no consensus successor and unconfirmed long-term governance for either. They're also diverging in direction — LadybugDB pivoting toward a "graph lakehouse" interoperating with DuckDB storage/Arrow/Parquet, Vela staying closer to Kuzu's original single-file embedded design while adding concurrent multi-writer support (the original enforced single-writer). Neither diligenced further given this much governance uncertainty.
- **`agentflare-ai/sqlite-graph`** — ruled out on its own terms: self-declared alpha (`0.1.0-alpha.0`), README states outright "Not recommended for production use"; Cypher support is incomplete (no variable-length paths — the core graph-traversal primitive), scale-tested only to roughly 1,000 nodes/edges.
- **Plain SQLite recursive-CTE traversal** — viable per the shallow-hop reasoning this document uses elsewhere, but forgoes genuine graph-query expressiveness. Documented as the fallback that would have been used had no SQLite-native Cypher option cleared diligence, not adopted since `graphqlite` did.

## Consequences

Concurrent-write safety was verified directly, not assumed: both node and edge writes were tested under genuine OS-level process concurrency (16 separate processes writing to the same db file simultaneously) — no corruption, no lost writes, no crashes, in both SQLite's rollback-journal and WAL modes (WAL ~4x faster under contention). `graphqlite`'s own architecture confirms why: concurrency here is inherited from SQLite's own transaction machinery, not a custom guarantee `graphqlite` has to earn independently.

A real correctness bug was found, not papered over: `CREATE`ing a relationship between two `MATCH`-bound nodes and then `RETURN`ing the relationship variable or a property on it raises `Error('Unknown variable: r')` — but the underlying `CREATE` commits anyway. Filed upstream as [colliery-io/graphqlite#95](https://github.com/colliery-io/graphqlite/issues/95); `GraphSearchBackend`'s implementation must never `RETURN` the relationship variable/properties in the same call as a `CREATE` linking pre-existing nodes.

Residual risk accepted deliberately: `graphqlite` is young (created July 2025), single-org-maintained, governance/bus-factor undiligenced beyond visible commit activity — a materially thinner track record than `sqlite-vec` had when [ADR-00021](00021-vector-engine-sqlite-vec.md) adopted it. Revisit if `graphqlite` stalls or the filed bug proves symptomatic of deeper transpiler correctness gaps.

This also closes [Vector search](../search/02-vector-search.md#interfaces-stay-separate-even-if-a-future-engine-could-serve-more-than-one)'s "share an engine across FTS/vector/graph" question by elimination rather than further diligence: `graphqlite` is Cypher-only, with no FTS/vector capability to share.

## Referenced from

- [Graph search → Concrete engine: `graphqlite`, a Cypher-to-SQL transpiler over the same SQLite file](../search/03-graph-search.md#concrete-engine-graphqlite-a-cypher-to-sql-transpiler-over-the-same-sqlite-file)
