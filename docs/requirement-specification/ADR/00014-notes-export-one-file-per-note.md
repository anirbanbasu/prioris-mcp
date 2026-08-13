---
status: accepted
date: 2026-08-01
---

# ADR-00014: Notes export is one file per note, not one file per document

## Context

`export` returns a note's file representation for the caller to write to disk (see [ADR-00007: Notes export as a resource, not a tool](00007-notes-export-as-resource.md) for why it's a resource at all). A document can have several notes attached to it over time, so export's granularity — one file per note, or one file per document aggregating all of that document's notes — needed deciding.

## Decision

`export` returns exactly one note per call: `{suggested_filename, frontmatter, markdown_body}` for one note, not an aggregate of every note attached to a document.

## Alternatives considered

- **One file per document, aggregating all of that document's notes** — rejected: it would require an in-file structure (internal anchors/headings) to separate notes within the file, and doesn't match the underlying data model 1:1 (each note is already its own row/id). One file per note also matches the more common Obsidian convention: atomic notes that link to each other and to a paper via frontmatter, rather than long files with internal structure.

## Consequences

`frontmatter` carries every non-`text` column verbatim as a plain JSON object; the caller renders it into whatever frontmatter dialect its target tool expects. `markdown_body` is exactly the note's own `text` column, unmodified.

## Referenced from

- [Storage → Notes storage → Export](../storage/02-notes-storage.md#export)
