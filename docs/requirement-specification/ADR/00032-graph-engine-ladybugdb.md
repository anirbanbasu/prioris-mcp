---
status: accepted
date: 2026-09-20
---

# ADR-00032: Graph search engine: LadybugDB, a native embedded Cypher engine

## Context

`GraphSearchBackend` needs a concrete storage/query engine for a single-layer knowledge graph (nodes/edges carry open metadata — provenance such as `human_annotated`/`llm_discovered`, and other differentiators — used to filter, rather than driving separate storage layers), following the same swappable-backend interface pattern already established for `EmbeddingBackend`/`VectorSearchBackend`. Two SQLite-based Cypher-on-SQL transpilers evaluated during earlier design work (`graphqlite`, `agentflare-ai/sqlite-graph`) fell short on exactly the two things this design needs: full, native Cypher rather than a compiled-onto-relational-schema subset, and an open per-node/per-edge metadata property bag rather than one requiring an EAV workaround.

## Decision

[LadybugDB](https://github.com/LadybugDB/ladybug) — an MIT-licensed, community-governed fork of KuzuDB (archived by Kùzu Inc. in October 2025 following Apple's acquisition of the company), implementing openCypher natively against its own columnar storage rather than transpiling onto a relational schema. Embedded, single binary/directory per database, no server process. Its property-graph model requires an upfront schema (node/relationship tables with typed columns), but its `MAP` type — a dictionary with a single key type and single value type, not required to share the same keys across rows — covers this design's open per-node/per-edge metadata bag natively, without an EAV workaround.

## Alternatives considered

- **`graphqlite`** — a Cypher-to-SQL transpiler over a stdlib `sqlite3.Connection`, extending the same file as every other backend. Rejected: transpiling Cypher onto a relational EAV schema is a structurally different architecture from a native graph engine, and surfaced three real correctness bugs under hands-on testing (`RETURN` after `CREATE`-with-`MATCH` raising while still committing; `upsert_edge` lacking independent identity from its `(source, target, rel_type)` triple; inline relationship-pattern parameter filters silently over-matching) — filed upstream as [colliery-io/graphqlite#95](https://github.com/colliery-io/graphqlite/issues/95), [#97](https://github.com/colliery-io/graphqlite/issues/97), [#96](https://github.com/colliery-io/graphqlite/issues/96). None is individually fatal (all three have workarounds), but they're symptomatic of the transpiler's own EAV schema being a second, implicit data model to reason about, not just Cypher.
- **`agentflare-ai/sqlite-graph`** — ruled out on its own terms: self-declared alpha (`0.1.0-alpha.0`), README states "not recommended for production use," and Cypher support is incomplete (no variable-length paths), scale-tested only to roughly 1,000 nodes/edges.
- **Upstream KuzuDB directly** — rejected: archived, no ongoing maintenance or fixes from the original team.
- **`Vela-Engineering/kuzu`** — a competing KuzuDB fork maintained by Vela Partners (an AI-focused VC firm) via Vela Engineering, adding genuine concurrent multi-writer support for multi-agent systems. Rejected — that capability alone solves a problem this project doesn't have (single local writer is sufficient at this project's scale/deployment model — see Consequences), and several other differences compound the case beyond that one point:
    - **Governance signal.** LadybugDB explicitly dropped the CLA and lets contributors retain copyright, signalling an intent to run as an open community project. Vela's fork carries no equivalent public governance commitment; it reads as a VC firm's internal engineering effort supporting its own portfolio's product, with community contribution invited but not structurally guaranteed.
    - **Independent development track.** LadybugDB has already shipped multiple releases past Kuzu 0.11.3 parity (e.g. v0.12.0 through v0.19.0 within weeks of forking), acting as a genuine ongoing successor. No evidence of comparable independent feature development was found for Vela's fork beyond its multi-writer patch — it reads as a targeted patch on a frozen base for one company's use case, not a general-purpose successor project.
    - **Unaudited differentiator.** The one capability Vela's fork adds over LadybugDB — concurrent multi-writer support — has no public documentation of its locking/isolation design and no independent correctness benchmarks found; the only claims ("production-tested") are marketing copy. Adopting it for a capability this project doesn't currently need would mean inheriting an unaudited concurrency-correctness surface for zero present benefit.
    - **Roadmap-drift risk.** The fork's stated contribution priority is "multi-agent memory patterns," i.e. shaped by Vela's own product roadmap rather than general graph-database usefulness — the same foreseeable-conflict shape [ADR-00021](00021-vector-engine-sqlite-vec.md) flagged for `sqlite-vector`'s commercially-motivated backer.
- **A hosted/client-server graph database (Neo4j, Memgraph, ...) now** — rejected as premature: no current requirement for multi-process concurrent write access, and a running service is a real operational cost this project's local-first, single-user posture doesn't need yet. The swappable-interface design preserves this as a later option without paying the cost now.

## Consequences

Two embedded persistence engines run in-process (SQLite for everything else, LadybugDB for the graph), with no cross-engine transactional atomicity between them — accepted, since nothing in this design needs a graph write and, say, a chunk/embedding write to commit atomically together. This departs from every other backend's practice of extending the same SQLite connection, but the earlier rejections of a second engine ([ADR-00021](00021-vector-engine-sqlite-vec.md), ChromaDB/LanceDB) were about paying that cost for *no offsetting benefit*; here the benefit is real — native Cypher rather than a transpiler's correctness surface.

LadybugDB allows exactly one `READ_WRITE` `Database` object on a given database at a time (concurrent readers, or concurrent read-only processes, are safe; a second concurrent writer is not) — the same single-writer ceiling SQLite itself has (WAL mode changes reader/writer blocking, not writer/writer). This is parity with, not a regression from, what an SQLite-based engine would have given anyway. A genuine future need for concurrent multi-process graph writes is resolved by swapping `GraphSearchBackend`'s implementation for a hosted/client-server graph engine, not by fighting the embedded engine's write model.

The upfront schema requirement (node/relationship tables declared before data is written, unlike Neo4j's schema-optional model) is a real constraint on the data-model design that follows this ADR: node/edge shape needs a small, fixed set of declared columns plus a `MAP`-typed metadata column, not a fully schema-free per-instance property bag.

Package/binding naming is still settling as of this writing (PyPI has seen `ladybug`, `real_ladybug`, and `real-ladybug` package names circulate during the project's post-fork rename from `kuzu`) — pin the exact package and verify its API surface against current docs at implementation time rather than assuming any Kuzu-era signature carries over unchanged.

## Referenced from

- [Graph search → Concrete engine: LadybugDB, a native embedded Cypher engine](../search/03-graph-search.md#concrete-engine-ladybugdb-a-native-embedded-cypher-engine)
