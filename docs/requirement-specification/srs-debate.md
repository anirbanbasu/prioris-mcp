---
icon: lucide/messages-square
---

# SRS debate: storage redesign coupling

!!! note "Working document"
    This is a temporary debate log, not a numbered SRS chapter — it exists to let the storage-redesign discussion below be picked up again later. Not linked from [the SRS index](index.md); delete or fold into the relevant chapter(s) once the debate concludes.

## Background

Commit `24cece4` ("docs: On-going updates to SRS for storage redesign issues") made substantial changes to [Storage](02-storage.md) and the files that reference it ([Architecture](01-architecture.md), [Functional requirements](03-functional-requirements.md), [Interface specification](06-interface-specification.md), [Test specification](07-test-specification.md)). Key changes in that commit:

- Renamed the storage "manifest" concept to **catalogue** (`catalogue.jsonl`), now a single top-level append-only index.
- Split what used to be two independent **format** values (`pdf`, `pdf-markdown`) into one `pdf` format directory containing two **artefacts** (`document`, `markdown`).
- Introduced a new directory layout keyed by a **document-hash** (`sha256([provider, canonical identifier])`, deliberately excluding `format` from the hash) with `format` as a nested path segment.
- Introduced `structure.jsonl` — PDF page/heading boundaries as `{start, length}` pointers into the `markdown` artefact, enabling a new `page` parameter on `parse_full_text`.
- Introduced a global SQLite + FTS5 full-text search index (`search.sqlite3`) and a new `research_search_fetched` tool.
- Reserved (not implemented) `summaries.jsonl` for a future `generate_full_text_summaries` tool.
- Changed deletion semantics to per-artefact (`document`/`markdown`/`all`) instead of per-format.

This document was asked to debate whether these changes are sound, starting from a specific worry: **does the redesign tie storage of raw content too tightly to the metadata/indexing machinery built on top of it (catalogue, structure, search)?**

## Debate so far

### 1. S3 portability of the redesign

The SRS's [Future: S3](02-storage.md#future-s3-or-other-remoteobject-backend) section claims `catalogue.jsonl` and the FTS5 index are "designed with [S3] in mind already." Assessment:

- The `documents/<document-hash>/<format>/...` layout genuinely generalises — object-store key prefixes map onto it cleanly.
- The claim that `catalogue.jsonl` is "append-friendly (no in-place edits, so no read-modify-write races on an object store)" **does not hold as written**: real object stores have no append operation — a single growing object requires a full GET+modify+PUT, which *is* a read-modify-write race under concurrent writers. A genuinely S3-safe catalogue would need an event-per-object log (e.g. `catalogue/events/<ts>-<uuid>.jsonl`, merged at read time, periodically compacted) — a different write path, not a drop-in `StorageBackend` swap.
- The "replay catalogue into an in-memory dict at process startup" model implicitly assumes a single writer process. This isn't S3-specific, but S3 is often adopted specifically to enable multiple server replicas — in which case this model has no coordination story at all.
- The FTS5 mitigation ("index can live on local disk, separate from durable data on S3") only works per-process: each replica's local `search.sqlite3` only reflects *that replica's own* writes, so `research_search_fetched` could silently return incomplete results under multiple replicas.

**Open thread:** whether to scope the "Future: S3" section down to "single-process, object-store-as-disk-replacement" explicitly, or treat multi-writer/multi-replica coordination as its own deferred concern.

### 2. Is the storage redesign over-coupling content storage with metadata/indexing?

Examined the `StorageBackend` interface (`exists`/`write`/`read`/`list`/`delete`/`search`) and its three metadata/index mechanisms component by component:

**`catalogue.jsonl` — coupling judged justified, not excessive.** It's a required key-value map from storage identity (provider/identifier/format/artefact) to metadata (timestamps, size, and — for the local filesystem source — the caller-facing-ID ↔ content-hash mapping). A `write` that didn't also record a catalogue entry would leave storage inconsistent. This generalises cleanly to any backend — a database-backed implementation would simply make it a table (e.g. S3 + DynamoDB is exactly this pattern).

**`structure.jsonl` (and, by extension, the reserved `summaries.jsonl`) — identified as the real design flaw, not just under-specification.** Current model: one monolithic `markdown` blob per format, with `structure.jsonl` as a sidecar of `{start, length}` pointers into it. This works for physically contiguous PDF pages, but doesn't extend to a RAPTOR-style hierarchical summary tree, because RAPTOR clusters semantically similar chunks (not necessarily page-aligned or contiguous) and summarises clusters recursively — a summary node has no single `{start, length}` range in the source markdown to point to. The SRS's current text hand-waves this by saying `summaries.jsonl` will "reuse" `structure.jsonl`'s shape, without that actually fitting.

Proposed alternative (from this debate, not yet written into the SRS): treat each page — and eventually each chunk — as its own first-class stored artefact, with a manifest as the source of truth for ordering/hierarchy, rather than one blob plus positional pointer files. This would unify three currently-separate, increasingly ad hoc mechanisms:

1. Native PDF structure (today's `structure.jsonl`) becomes the *leaf level* of a chunk manifest.
2. A future RAPTOR-style summary tree becomes higher levels of the *same* manifest (parent nodes referencing child chunk IDs plus their own generated-text artefact), not a new bespoke sidecar format.
3. Chunks become the natural unit for a future vector index (chunk → embedding) instead of needing another offset-based scheme carved out after the fact.

Acknowledged trade-off: many small stored objects instead of one blob-per-format costs more on S3 specifically (request count, not just bytes), and reconstructing "the whole document" needs manifest-driven concatenation instead of one read — but this is a standard shape for RAG-style document stores, and tractable at the SRS's stated target scale (tens of thousands of documents, not millions).

**`search`/FTS5 — agreed this is misplaced on `StorageBackend`.** The SRS's own language ("pure, disposable cache, never a source of truth," doesn't need to live on the same volume as durable data) contradicts bundling it into the same interface as `exists`/`write`/`read`/`list`/`delete`: an `S3StorageBackend.search()` method wouldn't touch S3 at all, it'd delegate to a local SQLite file — one method implemented against unrelated infrastructure from the other five is a standard signal the method doesn't belong on that interface.

**Extension agreed on:** pull `search` out into a separate, pluggable indexing abstraction (`SearchIndex` or similar), mirroring the existing interface-plus-swappable-implementation pattern `StorageBackend` itself already uses. This generalises further than FTS5 alone — a vector index and, later, a graph index both want *chunks* as their unit (not offsets into a blob), and a graph index specifically wants explicit chunk-to-chunk/parent-child relationships, which a chunk manifest (see `structure.jsonl` discussion above) provides close to for free. So the chunk-manifest fix and the indexing-abstraction fix reinforce each other rather than being independent changes.

## Where we left off / next steps

Not yet resolved or written into the SRS:

- Whether/how to scope the S3 section's claims (§1) more honestly, or split out multi-writer coordination as an explicitly separate future concern.
- A concrete chunk-manifest design to replace `structure.jsonl`'s blob-plus-pointer model — including what a "chunk" is for non-PDF formats (HTML/JATS have no native page concept at all).
- A concrete `SearchIndex`/indexing-provider interface, separate from `StorageBackend`, with FTS5 as the v1 implementation.
- None of `02-storage.md`, `01-architecture.md`, `03-functional-requirements.md`, `06-interface-specification.md`, or `07-test-specification.md` have been edited as part of this debate — this file is the only artefact produced so far.
