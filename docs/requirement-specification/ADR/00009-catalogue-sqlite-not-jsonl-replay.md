---
status: accepted
date: 2026-07-20
---

# ADR-00009: Catalogue as SQLite, not append-only JSONL-plus-replay

## Context

`catalogue.sqlite` is the single top-level index backing `list`/`exists`/`delete` — one row per `(provider, canonical identifier, format, artefact)` entry. It must support cheap reverse enumeration and safe concurrent writes, including from multiple local writer processes sharing the same storage root (e.g. two independent PriorisMCP instances launched by separate agent sessions).

## Decision

The catalogue is a SQLite table with a unique constraint on `(provider, canonical_identifier, format, artefact)`, written via `INSERT ... ON CONFLICT`.

## Alternatives considered

- **Append-only JSONL, replayed into memory at startup** (the design used in earlier drafts) — rejected: gives no atomic concurrent-write guarantee across multiple writer processes (relies on `O_APPEND` behaving), and requires an in-memory-replay-at-startup step that a plain indexed SQLite query doesn't need.

## Consequences

Listing is a plain indexed query. Concurrent writers sharing one local, properly lock-capable filesystem get atomic writes natively from SQLite's own locking, rather than an implicit single-writer assumption.

## Referenced from

- [Storage → The catalogue: `catalogue.sqlite`](../storage/01-document-storage.md#the-catalogue-cataloguesqlite)
