---
status: accepted
date: 2026-08-01
---

# ADR-00013: `notes-search.sqlite3` is deliberately minimal, not denormalized like `search.sqlite3`

## Context

`search.sqlite3` (see [ADR-00010](00010-fts5-plain-not-external-content.md)) denormalizes `provider`/`identifier`/`format` as `UNINDEXED` columns alongside its tokenized text, specifically so a query can be scoped to one document without needing separate per-document index files. Notes have their own FTS5 cache, `notes-search.sqlite3`, and the question is whether it should follow the same denormalization pattern.

## Decision

`notes-search.sqlite3`'s FTS5 table holds just a note `id` (`UNINDEXED`) and tokenized `text`. `search` applies every structured predicate directly against `notes.sqlite`, only intersecting with `notes-search.sqlite3`'s matching `id`s when a keyword is given.

## Alternatives considered

- **Mirroring `search.sqlite3`'s denormalized columns** — rejected: that pattern exists to avoid "separate per-document index files," a problem notes never had to begin with — there is already exactly one global `notes.sqlite`, not one per document — so the reason for denormalizing doesn't apply here.

## Consequences

Avoids duplicating columns across two files (one less place for them to drift apart), a reasonable trade against `search.sqlite3`'s single-query optimisation given the much smaller expected scale of a personal notes corpus versus "tens of thousands of documents." Sync is also simpler than `search.sqlite3`'s: `create`/`update`/`delete` each touch the FTS5 row directly and immediately (upsert or remove exactly one row), since notes are already row-granular and don't come from a bulk parse step.

## Referenced from

- [Storage → Notes storage → `notes-search.sqlite3` is deliberately minimal](../storage/02-notes-storage.md#notes-searchsqlite3-is-deliberately-minimal-a-departure-from-searchsqlite3s-precedent)
