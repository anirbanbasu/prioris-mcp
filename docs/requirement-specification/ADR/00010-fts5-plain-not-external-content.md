---
status: accepted
date: 2026-07-20
---

# ADR-00010: FTS5 as a plain virtual table, not an external-content table

## Context

`search.sqlite3`'s indexed unit is a `manifest.sqlite` `chunk`/`leaf` row's span, not a whole `markdown` artefact. The source of truth for that text is a separate `markdown` file on disk, not a row inside `search.sqlite3` itself.

## Decision

`search.sqlite3` is a plain FTS5 virtual table: its own `text` column holds only the matched chunk/leaf's span (never a whole document's Markdown), tokenized directly, alongside `provider`/`identifier`/`format`/`span_start` as `UNINDEXED` columns referencing that span's position.

## Alternatives considered

- **SQLite's `content=''` external-content table** — rejected: that mechanism requires the referenced original text to live in a rowid table inside the *same* SQLite database, which doesn't fit here since the source of truth is a separate `markdown` file on disk, not a row in `search.sqlite3`.

## Consequences

No duplication of a whole `markdown` artefact inside the search index. `span_start` is denormalised as its own column specifically so a result's reported offset means the matched entry's position in the document's own coordinate space — not FTS5's own `offsets()`/`snippet()`, which are relative to the indexed `text` column itself and don't match what `parse_full_text`'s `offset`/`limit` parameters need.

## Referenced from

- [Storage → Full-text search: the `search.sqlite3` index](../storage/01-document-storage.md#full-text-search-the-searchsqlite3-index)
