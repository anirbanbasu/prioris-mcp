---
status: accepted
date: 2026-08-12
---

# ADR-00021: Vector search engine — `sqlite-vec` (asg017)

## Context

`VectorSearchBackend` needs a concrete engine to store and query embeddings. This project's stated scale is thousands to tens of thousands of vectors (a single user's fetched-paper chunks plus notes), and it already follows a plain, embedded-`sqlite3` persistence pattern across `StorageBackend`, `SqliteFts5SearchIndex`, and `SqliteFts5NotesSearchIndex`.

## Decision

`sqlite-vec` (asg017) — MIT/Apache-2.0 dual-licensed with no commercial-use gate, precompiled wheels (0.1.9, manylinux + macOS arm64) so no new build toolchain, extending the same `sqlite3` stdlib connection pattern the rest of this project already uses (`conn.enable_load_extension()` + `sqlite_vec.load(conn)`, same `to_thread.run_sync` wrapper idiom). Its `vec0` virtual table does brute-force, exact KNN rather than true ANN (no HNSW/IVF-PQ).

## Alternatives considered

- **DuckDB + the `vss` extension** — ruled out on a reliability ground, not preference: HNSW index persistence is still experimental as of 2026, WAL recovery for custom index types isn't properly implemented, and a crash with uncommitted changes risks index corruption or data loss unless an explicitly-flagged experimental persistence mode is enabled. Would also introduce a second, unrelated embedded SQL engine alongside the existing plain-`sqlite3` stack, with no offsetting benefit given the persistence gap.
- **LanceDB** — a genuine vector database (HNSW/IVF-PQ ANN, native tantivy/BM25-backed full-text search, versioning), but built for a scale (large/billion-vector corpora) this project doesn't operate at. Introduces a second, structurally different persistence engine (Lance's own columnar format and directory layout) parallel to the `sqlite3`-file pattern every other backend already follows.
- **ChromaDB** — workable as an embedded engine (`PersistentClient`, Rust-rewritten 1.x core) but offers nothing LanceDB or sqlite-vec don't already do better here: ships telemetry on by default (needs explicit opt-out to match this project's local-first posture), has had real API churn (a breaking `Settings`-init removal in 0.4.0) and open bugs, and — like LanceDB — is a second persistence engine parallel to SQLite for no offsetting unique benefit.
- **Qdrant Edge** — ruled out on two independent grounds: in private beta with no stable public release, and its "full-text" story is sparse-vector-based (SPLADE-style), not a real inverted-index/BM25 engine — a materially different, non-fidelity-preserving mechanism relative to the literal/exact-term matching this project's citability requirements already depend on.
- **`sqlite-vector` (sqliteai)** — algorithmically comparable to sqlite-vec (also brute-force scanning, with SIMD quantization rather than true ANN — no scaling advantage either way), but dual-licensed: free for OSI-approved open-source use, with production/managed service use requiring a commercial license from its backing company, whose commercial interest is specifically hosted/managed SQLite services — a foreseeable conflict given [`index.md`'s scope](../index.md#v2) already names multi-user/hosted deployment as a stated future direction.

## Consequences

`vec0`'s brute-force exact KNN is a real ceiling, but one that lines up with this project's own stated scaling trigger: the point a large/shared corpus would actually need real ANN is the same point `index.md` already names for multi-user/hosted deployment, which is also naturally when a hosted `VectorSearchBackend` implementation would be swapped in behind the same interface.

## Referenced from

- [Vector search → Engine: `sqlite-vec` (asg017)](../search/02-vector-search.md#engine-sqlite-vec-asg017)
