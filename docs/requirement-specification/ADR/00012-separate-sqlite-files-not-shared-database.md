---
status: accepted
date: 2026-07-20
---

# ADR-00012: Separate SQLite files, not one shared database

## Context

`catalogue.sqlite`, each document's `manifest.sqlite`, and `search.sqlite3` are all SQLite (the same engine) and could technically share one physical file.

## Decision

They are kept as separate files. `search.sqlite3` is a pure, disposable cache, never a source of truth; `catalogue.sqlite` and every `manifest.sqlite` are durable.

## Alternatives considered

- **One shared SQLite database for catalogue, manifest, and search index** — rejected on two independent grounds:
    - Sharing a physical file with durable catalogue/manifest data would let a corruption event (a crash mid-write, a full disk) take out durable data because it happened to sit next to a disposable cache in the same file — undoing an isolation property this design deliberately builds in.
    - A single shared `manifest`-equivalent would lose the fine-grained locking that per-document `manifest.sqlite` files buy for free: unrelated documents never contend with each other, which matters once background work (re-chunking with a new `scheme`, a future summarisation pass) can touch one document's manifest independently of any other document's.

## Consequences

The overhead of one SQLite file per document is real but small at this store's target scale: an empirical measurement of a representative 8-row manifest (3 page-leaves plus 5 chunks — a small PDF) produced an 8,192-byte file in rollback-journal mode, against an 842-byte equivalent for the same rows serialised as JSON lines — roughly a 7.3KB fixed floor per document (WAL mode's `-wal`/`-shm` side files checkpoint away to nothing once a connection closes, so this floor doesn't compound with journal mode). This is a floor, not a per-row multiplier, so it matters proportionally more for a small document than a large one; at this store's stated scale (10,000-50,000 documents) it amounts to roughly 80-400MB of pure overhead — small next to the parsed-document corpus itself, but not zero. `search.sqlite3` doesn't need to live on the same (possibly cloud-mounted) volume as the durable files, either — it can live on local/ephemeral disk where available.

## Referenced from

- [Storage → Separate SQLite files, not one shared database](../storage/01-document-storage.md#separate-sqlite-files-not-one-shared-database)
