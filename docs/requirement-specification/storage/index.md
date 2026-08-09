---
icon: lucide/database
---

# Storage

PriorisMCP persists two logically separate things, covered as two separate chapters in this section:

- [Document storage](01-document-storage.md) — `StorageBackend`/`SearchIndex`: fetched full text and its parsed Markdown, content-addressed and write-once (v1).
- [Notes storage](02-notes-storage.md) — `NotesBackend`: user-authored notes about a document, identity-addressed and mutable (v2).

These are deliberately separate abstractions rather than one extended interface — see [Architecture → `NotesBackend`](../01-architecture.md#notesbackend) for why fetched-content semantics (content-addressed, write-once) and note semantics (identity-addressed, mutable) don't fit the same contract. Both currently target v1's single-machine, embedded-SQLite deployment model; see [Document storage → Embedded SQLite vs. a client-server database](01-document-storage.md#embedded-sqlite-vs-a-client-server-database) for the concurrency model this depends on and its limits, which [Notes storage](02-notes-storage.md) inherits rather than re-deriving.
