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

### 3. Revisiting "each page as its own first-class artefact": single blob + spans, not separate files

§2 proposed treating each page (and eventually each chunk) as its own first-class *stored artefact*. Follow-up debate revised the "own artefact" part specifically for pages, prompted by an analogy to email organisation: physical per-page files behave like sub-folders — a rigid, exclusive, one-way partition chosen once — whereas chunks (and future RAPTOR summaries) need something closer to labels/tags: non-exclusive, potentially overlapping, and able to span more than one "folder." You can't file one email in two folders, but a chunk can straddle two pages the way a label can span two folders' worth of mail.

This also surfaced a format asymmetry worth naming explicitly: PDF parsing yields genuine per-page structure, but JATS-XML/HTML parsing yields a single whole-document blob with no native page concept at all. Branching the artefact model by format (page-files for PDF, one file for everything else) was judged the wrong fix.

**Revised model:** one Markdown blob per (document, format) — PDF, JATS, HTML alike — with pages represented as a *span* entry-kind in the manifest (see §4), not as separate files. For PDF, the page-span level partitions the blob into N contiguous, ordered spans; for JATS/HTML, that level is trivially a single span covering the whole blob. Same schema either way, no per-format special case, and no more N-files-vs-1-file asymmetry.

Re-examining the original justifications for page-per-file against this revision:

- **S3 request-count actually favours one blob + range-`GET`s.** §1 already flagged that many small objects cost more in request count than bytes on S3; N page objects makes that worse, not better. One blob plus range reads (S3 supports byte-range `GET`) is fewer requests.
- **Write-once immutability doesn't argue either way.** The content blob is written once at parse time and never mutated regardless of whether it's split into page files — this justification doesn't distinguish the two models (see also the correction in §5 about where write-once *does* and doesn't apply).
- **Per-page content-addressed dedup is lost, but judged minor.** Physically separate, content-hashed page files would let identical boilerplate pages (cover sheets, licence pages) recurring across documents be stored once. Under one-blob-per-document this is lost. At the SRS's stated scale this is a few KB per document at most — negligible against document bodies. If dedup ever matters more, it's better solved later at the chunk/embedding level (hash chunk content before requesting an embedding, skip if already embedded) than by reintroducing physical page files.
- **The RAPTOR objection from §2 is orthogonal to this question.** Summary nodes having generated text with no span into the source is true regardless of whether pages are files or spans — it's fixed by the manifest having a distinct "summary" entry kind (§4), not by how pages are stored.
- **The one scenario that would still favour physical per-page separation:** sub-document deletion (e.g. "expunge just page 12"). Not a current requirement — today's deletion semantics are per-artefact (`document`/`markdown`/`all`), not per-page. Flagged as the condition that would tip this back toward physical files if it ever becomes a requirement.

**RDBMS mapping also favours this model.** `artefacts(document_hash, format, content)` plus `manifest(artefact_id, kind, start, length, parent_id, ordinal)` expresses a cross-page chunk as two manifest rows sharing a `parent_id`/ordinal sequence, sliced from the same `content` column — no join across separate per-page rows needed. (Per-page-file/row model, moved to an RDBMS, would naturally become one row per page, reintroducing the same rigidity via joins instead of folders.)

### 4. Manifest entry kinds: leaf / chunk / summary

Independent of the files-vs-spans question in §3, the manifest needs (at least) three entry kinds:

1. **leaf** — the page-span level: parser-determined, single span each, ordered, exhaustively partitions the blob (or is a single span for whole-document formats like JATS/HTML).
2. **chunk** — algorithm-derived, and structurally a *list* of spans rather than one `{start, length}`, so a chunk can cross page boundaries by concatenating spans from adjacent page-leaves in order. Multiple coexisting/versioned chunking schemes (e.g. a coarse scheme for FTS5, a finer one for a future vector index) can coexist as separate chunk entries over the same underlying leaves, without duplicating content — the tag-like property from §3's analogy.
3. **summary** (future, RAPTOR) — not a span at all: its own generated-text artefact, referencing child chunk/summary IDs. This is what actually resolves the `summaries.jsonl` objection from §2, rather than `summaries.jsonl` "reusing" `structure.jsonl`'s shape as the current SRS text hand-waves.

### 5. Catalogue's real justification, and `structure.jsonl`'s shared exposure to the S3 append problem

Refines §2's "coupling judged justified" verdict on `catalogue.jsonl`:

- **Forward lookup needs no catalogue at all.** The document-hash is deterministically `sha256(provider, canonical_identifier)`, computable on the spot from a request — that was the point of hashing on exactly those two fields. Catalogue was never needed for this.
- **What catalogue actually buys is cheap reverse enumeration.** Hash-named directories/objects don't carry the original identifier back out. Listing (a `list` tool, or the S3 in-memory-replay-at-startup model) otherwise means opening every document's own metadata — O(n) opens — versus reading one aggregated index — O(1) reads for O(n) entries.
- **For the local-file source specifically, catalogue is more than an optimisation.** Its caller-facing ID has no deterministic hash formula at all (it's an arbitrary caller-chosen name/content, not a canonical identifier), so catalogue is the *only* place that ID↔hash mapping exists.

**Correction to §1:** the "write-once removes the S3 race" reasoning (used earlier in this debate to argue the content blob doesn't have `catalogue.jsonl`'s append problem) only ever applies to the *content blob itself*. `structure.jsonl` (or its manifest successor) is not write-once the same way: chunk-span entries get added whenever a new chunking scheme runs, summary entries get added later still once RAPTOR generation exists. So the manifest inherits the same S3 append/read-modify-write exposure already flagged for `catalogue.jsonl` in §1, and needs the same fix — an event-per-write log (e.g. `structure/events/<ts>-<uuid>.jsonl`, merged at read time, periodically compacted) — not just catalogue.

Catalogue remains necessary on S3 too, for the same reverse-listing/local-file-ID-mapping reasons as local disk — just implemented as an event log rather than one growing object, matching §1's proposed fix.

**RDBMS equivalent:** a `documents(document_hash PK, provider, canonical_identifier, created_at, size, ...)` table is a strict improvement over the JSONL-plus-replay design, not just an equivalent — a unique constraint / `INSERT ... ON CONFLICT` gives atomic concurrent writes natively, and listing is a plain indexed query, with no in-memory-replay-at-startup step needed at all.

### 6. Embedded SQLite vs. client-server database

Resolves §1's open thread on scoping multi-writer/S3 coordination.

- **SQLite genuinely solves the atomicity problem for `catalogue`, `structure`, and free-text search for any number of writer processes sharing one local, properly-lock-capable filesystem.** This includes multiple MCP server instances on the same machine — e.g. two parallel Claude Code sessions each launching their own MCP server against the same local storage root. SQLite's file-locking protocol is exactly what this scenario is designed for; it is not a "single process only" limitation, and is a strict improvement over raw JSONL appends (real transactions instead of hoping `O_APPEND` behaves).
- **SQLite does not work across machines/replicas**, nor over a network-mounted filesystem (NFS/SMB) or a consumer file-sync client (Dropbox, Google Drive, OneDrive, etc.): it has no distributed-replication story of its own, and file-locking semantics are unreliable-to-nonexistent over those substrates (S3 has no locking or in-place-write primitive at all — SQLite cannot be pointed at it directly as a live, mutable store). This is the same underlying limitation §1 already flagged for the FTS5 index ("each replica's local `search.sqlite3` only reflects that replica's own writes") — one root cause (SQLite is a single-node embedded engine) showing up in three places (catalogue, structure, search), not three separate risks.
- **Deployment imperative:** any deployment where writer processes cannot share a single local, properly-lock-capable filesystem (i.e. genuinely different machines/replicas) MUST use a real client-server database (e.g. Postgres, or a distributed SQLite-compatible option) for catalogue/structure/search instead of embedded SQLite. This is stated on writer-process topology, not on whether content blobs live on S3 — the two axes usually coincide (S3 tends to get adopted specifically to enable multiple replicas) but aren't logically identical; e.g. a single dedicated metadata-service process could still use embedded SQLite even with blobs on S3.
- **Restriction:** the local-filesystem storage root (JSONL or SQLite alike) MUST NOT reside inside a directory managed by a consumer cloud-sync client (Dropbox, Google Drive, OneDrive, etc.) or a network-mounted filesystem (NFS/SMB). These sync/mount layers don't understand the storage backend's locking/journaling protocol and can propagate a torn write or produce unmergeable "conflicted copies" — SQLite's own documentation warns against exactly this. Applies to the whole local-filesystem backend, not just the embedded-database option.
- **Decision:** stay with SQLite; no alternative engine adopted. Any alternative embedded engine (e.g. ObjectBox, floated during the debate) would need its own explicit multi-process concurrency audit before being treated as interchangeable — SQLite's decades-proven locking guarantees are specific to SQLite, not assumed to generalise to "SQLite-compatible" alternatives.

### 7. Physical layout: separate SQLite files, not one shared file

- **Catalogue, per-document structure/manifest, and free-text search should be separate SQLite files** (same engine, not the same file/database), even on a single machine. Reason: the SRS already treats the search index as a "pure, disposable cache, never a source of truth" (§2); sharing a physical file with durable catalogue/structure data would let a corruption event (crash mid-write, disk full) take out durable data because it happened to sit next to a disposable cache in the same file — undoing an isolation property the SRS deliberately built in.
- **Layout:** one `catalogue.sqlite` (top-level, shared across all documents), one manifest file per document-hash directory replacing `structure.jsonl` (naming TBD), and one disposable `search.sqlite3` (as already specified).
- **Per-document manifest files also buy fine-grained locking for free:** unrelated documents never contend, which matters once background jobs (re-chunking, future RAPTOR summary generation) can touch a document's manifest independently of the original parse request.
- **Measured cost of per-document SQLite files** (empirical test run in this session, not estimated from memory): a representative manifest of 8 rows (3 page-spans + 5 chunk-spans — a small PDF) produced an **8,192-byte** SQLite file in rollback-journal mode (WAL mode's `-wal`/`-shm` side files checkpoint away to nothing at rest once the connection closes), versus an **842-byte** JSONL equivalent for the same rows — roughly a 7.3KB fixed floor per document, ~10x the JSONL size for a small document. This is a floor, not a per-row multiplier, so it matters proportionally more for small documents than large ones. At the SRS's stated scale (10,000-50,000 documents) this is roughly 80-400MB of pure overhead — small next to the actual parsed-document corpus, but not zero.
- **Journal mode (WAL vs. rollback) deferred** to implementation/configuration time — doesn't affect the schema or file layout decided here.
- **Backup/restore for the embedded-DB local backend deferred** as separate future functionality, not designed here — noting only that a naive file copy of a live SQLite file mid-write isn't safe; it needs the engine's own consistent-snapshot mechanism (backup API / `VACUUM INTO`).
- **Loss of human grep/cat-ability judged minor, not a real capability loss:** the `sqlite3` CLI (including `-json` output, pipeable into `jq` if desired) is as ubiquitous and close to single-command as `jq` was for JSONL.

### 8. What is a "chunk"? Source-document structure, not Markdown pattern-matching

The idea of a "chunk" originated from background thinking about vector indexing — but indexing (chunking included) was already agreed in §2 to live in a separate, pluggable `SearchIndex`-style abstraction, never in `StorageBackend`. This section is about what a chunk *is*, independent of which subsystem consumes it.

**Reframe:** a chunk is a structural unit of the *original source document* — e.g. Article 13 of the EU AI Act, which spans PDF pages 59–60 and is bounded by its own heading — not a pattern (sliding window, or heading-regex over already-flattened Markdown) applied downstream after parsing. This means chunk detection belongs at **parse time**, alongside leaf/page detection, using the same `{start, length}` + a title/name key pattern that leaf already uses (leaf's key is the page number; chunk's key is the heading/section title).

**Leaf vs. chunk, restated:** leaf is a physical/positional partition — exhaustive, contiguous, mandatory even when trivial (N=1 for whole-document formats). Chunk is a semantic partition — optional, parser-derived when possible, and free to cross leaf boundaries.

**Resolved — keep list-of-spans, but not for the reason originally suspected.** §4 said a chunk is "structurally a list of spans... so a chunk can cross page boundaries." Since leaves are contiguous and exhaustively partition the blob (§3), a chunk crossing a page boundary is in fact always a single contiguous range in blob coordinates. Checked against the actual dependency rather than assumed: `liteparse`'s `keep_headers_footers` defaults to `False` ("stripping repeated page-band lines and page chrome"), and `pdf_liteparse.py` doesn't override it — running headers/footers/page numbers never reach the blob in the first place, so the discontiguity source originally suspected here doesn't exist for this pipeline. A single `{start, length}` would have sufficed for every case examined, including tables reconstructed inline from spatial layout (see §12).

List-of-spans is kept anyway, as a deliberate generalisation rather than a proven-necessary shape: it costs nothing for the common case (stored as a one-element list), and leaves room for a case that *does* need it without a later schema migration — e.g. an LLM-derived fallback chunk (this section's `source: llm` case) splicing together non-adjacent, semantically related passages, which is a real discontiguity source parser-derived chunks don't have.

**Agreed — chunking is an SRS-level conditional capability:** the parser MUST emit chunk entries when it can recover source-document structure; when it cannot, no chunk entries are produced for that leaf/document, and callers needing sub-leaf granularity fall back to leaf-level only. This is deliberately honest about parser fidelity limits (see §9) rather than overpromising uniform chunk support.

**Agreed — chunk entries need a provenance marker (`source: parser | llm`):** parser-derived chunks are free, deterministic, a byproduct of parsing. LLM-derived chunks (a fallback when structure can't be recovered — see §9 on PDF) cost a model call and are non-deterministic, the same operational risk category as summaries, not the same guarantee as parser-derived chunks. These should not share one undifferentiated row shape/meaning in the manifest.

### 9. Format-by-format chunk fidelity

- **JATS-XML** was initially assessed as the best case, since the schema has explicit `<sec>`/`<title>` structure with no inference needed. That assessment held up in principle, but see §11 for what was found on checking the *actual* Europe PMC parsing code rather than assuming.
- **HTML**: the boilerplate problem (nav/sidebar/menus/inline alerts) is largely pre-mitigated by the already-decided use of trafilatura for main-content extraction (implementation detail, deliberately not in SRS text yet — see [[project_prioris_mcp_srs]]). Heading recovery within the retained article content should be reliable, since it's explicit DOM markup, not visual inference.
- **PDF** is the genuinely risky format: heading detection relies on visual/font-based heuristics rather than explicit markup, and is bounded by the earlier LiteParse-vs-docling tradeoff (LiteParse chosen specifically to avoid docling's heavyweight PyTorch dependency; docling's superior ML-based structure recovery was rejected on those grounds). §8's conditional-capability requirement exists specifically to make this honest at the SRS level rather than assume PDF chunking always works.

### 10. Summaries connect to chunks, not (only) leaves

§4 originally said summary nodes reference "child chunk/summary IDs" — never leaves. Flagged this session as an unacknowledged gap: RAPTOR's base clustering layer is normally the chunk layer, not pages, since pages are too coarse and heterogeneous a unit to cluster meaningfully.

**Agreed:** summaries should reference chunks, not leaves, as the base layer. This holds even for JATS/HTML, which have only one (trivial) leaf each — because they can still have many parser-derived chunks (heading-bounded sections), giving hierarchical summarization a real base layer to build on despite the single-leaf case.

**Not resolved:** the degraded edge case where a document has *zero* chunks at all (e.g. a PDF where structure recovery failed and no LLM-chunking fallback was invoked) — whether summary falls back to referencing leaves directly in that case was raised but not reconfirmed after the general chunks-not-leaves principle was agreed.

### 11. JATS parsing pipeline: shared XSLT→HTML→Markdown vs. a dedicated JATS→Markdown XSLT

Checked the actual Europe PMC implementation rather than assuming: `jats_xslt.py` runs a vendored NCBI stylesheet (`jats-html.xsl`, from JATSPreviewStylesheets) to transform JATS to HTML, then hands off to the same `MarkdownifyHtmlBackend` used for arXiv's fetched HTML (`html_markdownify.py`) — one shared HTML→Markdown implementation, per that file's own comment, deliberately avoiding a second bespoke JATS converter.

What the vendored stylesheet preserves: `<sec>` → `<div class="section">`; title nesting depth is XPath-matched to `<h2>` (`body/*/title`, main-title) / `<h3>` (`body/*/*/title`, section-title) / `<h4>` (`body/*/*/*/title`, subsection-title) — `jats-html.xsl:1976-2018`.

Concrete losses identified by reading the stylesheet:

1. The `<div class="section">` wrapper doesn't survive `markdownify` (it drops generic divs) — section boundaries in the final Markdown must be re-inferred positionally from heading levels, the same heuristic any Markdown document requires. Judged harmless for chunking purposes, not a real capability loss.
2. `sec-type` (JATS's semantic section classification — methods/results/etc.) doesn't appear anywhere in the stylesheet's HTML output. Genuinely lost.
3. **A concrete bug:** the depth-aware mapping only distinguishes 3 levels. Anything nested deeper than subsection falls through to the generic `match="title"` template (`jats-html.xsl:2037-2043`), which emits a plain `<h3 class="title">` — the same level as a genuine top-level section. A JATS document nested 4+ levels deep would alias a deep subsection to look like a peer of a top-level section.

**Proposed fix (mine, not accepted):** patch only the title-depth-mapping templates in the existing vendored stylesheet to compute heading level dynamically (`count(ancestor::sec)`) instead of the fixed XPath enumeration, leaving the rest of the vendored stylesheet's handling of tables/MathML/figures/cross-references/footnotes/citations untouched.

**Counter-proposal (the user's, restated explicitly at the end of this session):** a custom, from-scratch JATS-XML → Markdown XSLT with **no HTML intermediate at all**.

- *For:* removes reliance on `markdownify`'s independently-versioned, opaque rules for the JATS path specifically; XSLT is a natural fit for well-formed XML (which JATS is, unlike arbitrary scraped HTML); fully fixes the depth-aliasing bug via computed depth rather than a patch.
- *Against, raised but not resolved:* the vendored stylesheet's HTML output isn't "wild" HTML — it's synthetic and generator-controlled, so the usual "HTML is messier than XML" argument for preferring XSLT is weaker here than it first appears. A full custom Markdown-emitting stylesheet has to reimplement the vendored stylesheet's hard-won handling of tables, MathML, figures, cross-references, footnotes, and citations — all harder to emit correctly in Markdown than HTML (Markdown tables are far more limited than HTML `<table>`; MathML has no native Markdown representation at all). It also does not reduce "two implementations" to one: `markdownify` still has to stay for arXiv's genuinely-wild fetched HTML, so a bespoke JATS→Markdown XSLT would sit *alongside* it as a second, larger, parallel converter — increasing, not decreasing, the duplication `html_markdownify.py`'s own design comment says the current architecture was built to avoid. It also doesn't solve chunk-offset computation: XSLT text-mode output has no built-in notion of the current byte offset in the emitted stream, so a post-hoc heading-walk over the resulting Markdown is still needed regardless of which pipeline produced it.

**Open thread, unresolved at end of session:** (a) keep the shared XSLT→HTML→`markdownify` pipeline and patch only the depth-mapping bug, or (b) build a dedicated JATS-XML→Markdown XSLT with no HTML intermediate, accepting the larger maintenance surface and continued duplication with `markdownify` on arXiv's HTML path. Not decided.

### 12. Extracted PDF image artefacts

Follow-up to §8's list-of-spans question: while checking whether table/image reconstruction could be a source of chunk discontiguity, `liteparse`'s actual behaviour (checked against `types.py`/`parser.py`/the package's own `METADATA`, not assumed) turned out to differ between the two content types:

- **Tables** are rendered inline as Markdown table syntax, "reconstructed from the spatial layout" per `liteparse`'s own README — no separate storage; whatever position they land in during reconstruction is their position in the blob, so chunk contiguity holds by the same argument as §8's header/footer finding. The only residual risk is layout-reconstruction *fidelity* ("reconstruction quality varies with document complexity" — the package's own caveat), the same visual-heuristic risk §9 already flagged for PDF generally, not a new discontiguity source.
- **Images** are different: with `extract_images=True`, raw image bytes come out as separate `ExtractedImage` objects, and the Markdown blob only carries a placeholder/reference whose `id` "matches the reference used in the markdown output" (`liteparse/types.py:191`). The reference stays inline in the blob (contiguity holds), but the actual image bytes have no storage home in the current `StorageBackend` model at all — a genuinely new gap, not a restatement of the span-shape question.

**Agreed:** image bytes get a new artefact type alongside `document`/`markdown`, in the same document-hash directory (`documents/<document-hash>/pdf/images/<image-id>`, naming TBD) — extending the existing artefact model rather than inventing a parallel storage mechanism.

**Not resolved / not yet designed:**

- Whether `liteparse` should even be configured to extract images (`extract_images=True`) given no current tool consumes image bytes — configuring `image_mode="placeholder"` or `"off"` to skip extraction entirely is the simpler default until a concrete tool needs them.
- If extraction is enabled: catalogue/manifest entries for image artefacts, deletion semantics (does deleting the `markdown` artefact also delete images it references?), and whether `research_search_fetched`/FTS5 should index anything about images (e.g. alt text) at all.

### 13. JATS pipeline resolution: html-to-markdown swap + narrow MathML pre-pass, no dedicated JATS XSLT

Resolves §11's open thread. Investigated further by testing actual libraries rather than reasoning from either option's marketing copy or the debate's own earlier assumptions:

- Confirmed §11's depth-aliasing bug directly against the stylesheet rather than trusting the earlier summary: `jats-html.xsl:1976-2018` gives three explicit depth-keyed templates (`<h2>` main-title, `<h3>` section-title, `<h4>` subsection-title), and `jats-html.xsl:2037-2043`'s generic `match="title"` fallback (anything nested deeper) emits plain `<h3 class="title">` — the same tag as the genuine section-title template. Since `markdownify`/`html-to-markdown` both key heading level off tag name only, never `class`, a section nested 4+ levels deep renders identically to a genuine top-level section. Bug confirmed real.
- Looked at what else is lost at the `markdownify` step specifically (as distinct from the XSLT step, which preserves table/MathML/image content faithfully through to HTML):
  - **Tables**: JATS's `<table-wrap>` content is copied straight through to genuine XHTML `<table>`/`<tr>`/`<td>` with attributes intact (`jats-html.xsl:2582-2589`) — nothing lost until `markdownify` runs. Tested empirically with a `rowspan`/`colspan` table: `colspan` degrades gracefully (empty-cell padding, column alignment preserved), but `rowspan` is silently mishandled — the spanned cell's sibling row shifts left with no marker, so a value reads as belonging to the wrong column. Wrong data, not just lossy formatting.
  - **MathML**: the stylesheet deliberately passes `mml:*` through verbatim (`jats-html.xsl:2518-2519`, comment: "this stylesheet simply copies MathML through"). Tested `E=mc²` through `markdownify`: comes out as `E=mc2` — the superscript is silently dropped, producing a plausible-looking but wrong token.
  - **Images**: `graphic`/`inline-graphic` only ever carries `@xlink:href` (`jats-html.xsl:2185-2194`, `assign-src` at `:3689`), typically a bare Europe PMC asset identifier, never embedded bytes — unlike PDF (§12), JATS parsing has no image bytes available at parse time at all; fetching them would need a separate network mechanism, out of scope here.
- Checked whether an alternative HTML→Markdown library fixes the rowspan/MathML problems, rather than assuming a full dedicated-XSLT rewrite is the only fix available. `html-to-markdown` (PyPI; MIT-licensed; Rust-core via prebuilt wheels for the project's supported platforms, not a system-binary dependency the way Pandoc would be; `>=3.10`) — a from-scratch rewrite, not a `markdownify` fork — was tested against the same two cases:
  - **Rowspan/colspan: fixed.** Same input, correct output — the rowspan'd cell's sibling row stays in the correct column, empty-padded rather than shifted.
  - **MathML: not fixed.** Identical `E=mc2` flattening — no HTML→Markdown library tested has MathML awareness; this gap exists independent of library choice, since Markdown itself has no native math representation.
- `markdownify` (the pip package) turned out to be used in exactly one place in the codebase: `html_markdownify.py`. `server.py` only imports the wrapper class (`MarkdownifyHtmlBackend`), and `pyproject.toml:29` is the only dependency reference. No other call site depends on `markdownify`'s specific behaviour, so swapping the underlying library in that one file covers both consumers — arXiv's fetched HTML and JATS's XSLT→HTML output — automatically.

**Resolved:**

1. **No dedicated JATS→Markdown XSLT.** §11's strongest argument for one ("fully fixes the depth-aliasing bug via computed depth," implicitly also motivated by table/MathML fidelity) turned out to be available more cheaply on the shared pipeline once the actual libraries were tested rather than assumed. The original §11 debate weighed the *architecture* question without first checking whether a library swap alone would close the table/rowspan gap — it does.
2. Swap `markdownify` → `html-to-markdown` in `html_markdownify.py`, removing `markdownify` from `pyproject.toml` entirely. Fixes rowspan/colspan for both JATS and arXiv HTML at once, with no bespoke stylesheet needed.
3. Add a narrow XSLT template converting `mml:*` subtrees to inline plain-text LaTeX (e.g. `$E=mc^2$`) *before* handoff to the HTML→Markdown backend, rather than passing MathML through verbatim as the vendored stylesheet currently does. This is the one piece of the original "no HTML intermediate" idea worth keeping, scoped to just the subtree that actually needs it rather than the whole document.
4. Patch the depth-mapping templates (`jats-html.xsl:1976-2018`, `:2037-2043`) to compute heading level dynamically (`count(ancestor::sec)` or equivalent) instead of the fixed 3-level XPath enumeration.
5. Rename `MarkdownifyHtmlBackend`/`html_markdownify.py` to something library-neutral (e.g. `HtmlToMarkdownBackend`) since the class no longer wraps `markdownify` specifically — including the module's docstring, which currently frames itself around `markdownify` by name.

**Not yet designed:**

- The `mml:*` → LaTeX XSLT mapping itself — only sketched as a concept (`E=mc^2` is a trivial case); non-trivial MathML (fractions, integrals, matrices, multi-line equations) needs its own mapping design, not assumed to fall out for free.
- Whether the depth-mapping fix needs to generalise beyond `<sec>`-based nesting (body matter) to abstract/back-matter nesting, which may not use uniform element names — flagged in the original §11 debate, not re-checked this session.
- `tests/test_parsers_html_markdownify.py` will need its golden-output assertions updated for `html-to-markdown`'s exact output conventions (whitespace, heading-style equivalents) even where behaviour is equivalent to `markdownify`'s.

### 14. Summaries/RAPTOR deferred until vector search exists

Prompted by revisiting §10's zero-chunk-fallback question against the actual motivation for wanting RAPTOR-style summaries in the first place: efficient semantic drill-down for retrieval — e.g. navigating a 144-page document like the EU AI Act down to Article 13 and its cross-references, without dumping all leaves into an LLM's context window.

Examining this against the SRS's actual current retrieval capability:

- RAPTOR's own retrieval mechanisms — collapsed-tree (flatten all node levels, rank by embedding similarity) and tree-traversal (descend from root into the most-similar branch, level by level) — are both inherently vector/embedding-based. Neither has an established equivalent for keyword search: FTS5/BM25 has no continuous "how similar is this branch" signal, only literal term matching, so genuine tree traversal isn't achievable with FTS5 alone.
- What a summary hierarchy could still offer a pure-FTS5 system is more modest: paraphrased vocabulary added to the index (improves recall for queries phrased differently from the source text), and an optional two-pass "search summaries, then restrict to that node's children" pattern — but the latter isn't real tree traversal, and both benefits are marginal for literal/referential document types (legal/technical text, this project's actual domain), where a direct FTS5 keyword search for e.g. "Article 13" already surfaces cross-references well, at a corpus size (hundreds of leaves) FTS5 handles efficiently without needing help avoiding brute-force scanning.
- The bottleneck originally motivating this thread — too much irrelevant text reaching the LLM's context window — is a result-set-size/ranking problem, which reasonable FTS5 top-k ranking already addresses to a first approximation; it doesn't inherently require a pre-built summary tree to solve.

**Agreed: summaries/RAPTOR is deferred in its entirety until the vector index (already deferred as future work in §2) is designed.** Its actual payoff (efficient semantic drill-down) is gated on vector search existing at all — building `summaries.jsonl`/`generate_full_text_summaries` now would mean paying real costs (LLM summarization calls, non-determinism, manifest schema complexity, and the zero-chunk/zero-leaf edge cases raised in §10) for a capability that can't deliver its main benefit yet.

**Consequence:** §10's open zero-chunk-fallback question (and any further RAPTOR-specific manifest design) is not resolved here — it's out of scope until vector search's own design is scoped, at which point it should be revisited together with vector search rather than independently, since the right answer may depend on decisions made there (e.g. what base retrieval unit vector search ends up using).

### 15. Manifest table schema

Resolves the "concrete manifest table schema" item from the previous session's "still not resolved" list, for the per-document manifest file introduced in §7 (replacing `structure.jsonl`).

**One table, one row per entry** (leaf, chunk, or summary):

| column | meaning |
|---|---|
| `id` | PK (rowid) |
| `format` | `pdf \| jats \| html` — disambiguates which format's Markdown blob this entry's spans index into, since one document-hash directory (and therefore one manifest file, §7) can hold multiple formats, each with its own independent blob and coordinate space |
| `kind` | `leaf \| chunk \| summary` (§4) |
| `key` | flat text: page number for `leaf`, heading/section string for `chunk`. For `summary`, left genuinely open rather than assumed-null — summary itself is undesigned (§14), and nothing rules out an LLM-generated cluster label playing the same role a heading does for chunks |
| `provenance` | `parser \| llm`, **non-null on every row, not chunk-only**: always `parser` for `leaf` (deterministic parser output by definition, no LLM-derived-leaf concept exists); the only kind where it actually varies is `chunk` (§8's parser-structure-recovery vs. LLM-fallback case); always `llm` for `summary` under every summarization mechanism discussed so far (RAPTOR-style `generate_full_text_summaries` is inherently model-generated) — but this is provisional, since summary is undesigned, not an enforced constraint |
| `scheme` | chunk-only, free text (not a DB-level enum/`CHECK`/lookup table) — names a chunking algorithm+config (e.g. `"heading-bounded-v1"`, `"fts5-coarse"`, a future `"vector-fine-v2"`), letting multiple coexisting chunking passes (§4) layer on without a schema migration each time. Different in kind from `provenance`: `provenance` is a closed, small structural category (exactly two ways an entry can be derived); `scheme` is an open, growing, application-owned naming convention that only the chunking subsystem needs to interpret |
| `spans` | JSON array `[{start, length}, ...]` (SQLite JSON1) — null for `summary`. Chosen over a normalized `spans` child table because the list is read/written atomically as a unit and is a one-element list in the common case (§8) |

**Dropped `ordinal` entirely.** It would have served two different purposes, both already covered without it: span-internal order comes from JSON array order (the array is inherently ordered, unlike a normalized child table needing its own sequence column); entry-level document order comes from sorting by the first span's `start` (or, for `leaf`, the numeric `key`/page number directly).

**Nesting (e.g. Article 13 → 13.1 → 13.1.2) is derived, not stored.** No `parent_id` and no hierarchical key. A subsection's span is a sub-range of its parent section's span, so containment among `chunk` entries sharing the same `(format, scheme)` reconstructs the hierarchy on demand. Scoped to one `scheme` deliberately: two different chunking passes' boundaries aren't guaranteed to align, so containment across schemes wouldn't mean anything.

**Agreed (this session, superseding the earlier flat-chunk framing in §4/§8):** chunk entries capture every heading nesting level as separate, overlapping entries (an H3 subsection and its enclosing H2 section both get their own row), not just one chosen granularity — needed so a future summary tree (§14, still deferred) has an actual hierarchy to cluster over once it's designed.

**Not yet designed / left open:**

- Whether `summary` will end up with a real `key` (cluster label) once summary design actually happens (§14) — deliberately not decided here.
- Whether `summary`'s `provenance` is unconditionally `llm` or could ever be something else (e.g. a future non-LLM extractive method) — treated as the expected value, not an enforced constraint.
- Indices/generated-columns for span-range queries (e.g. supporting the `page` parameter on `parse_full_text`, or "which entries cover byte X") weren't designed this session.

## Where we left off / next steps

Resolved this session, not yet written into the SRS:

- Pages (and whole-document formats like JATS/HTML) are spans in a single per-(document, format) Markdown blob, not separate per-page files (§3).
- Manifest needs three entry kinds — leaf, chunk (multi-span, crosses page boundaries), summary (generated artefact, not a span) (§4).
- Catalogue's justification is reverse-enumeration and local-file-source ID mapping, not forward lookup; `structure.jsonl`'s successor shares `catalogue.jsonl`'s S3 append exposure and needs the same event-log fix (§5).
- SQLite is retained for the single-machine case (any number of local writer processes); a real client-server database is required only when writer processes can't share one local, lock-capable filesystem — stated on writer-process topology, not S3 adoption (§6).
- Local storage root (JSONL or SQLite) must not live inside a consumer cloud-sync folder or a network-mounted filesystem (§6).
- Catalogue, per-document structure, and search stay as separate SQLite files rather than one shared database, to preserve durable-vs-disposable isolation; per-document SQLite overhead measured empirically at ~8KB floor, ~10x a small JSONL equivalent, judged acceptable at stated scale (§7).
- A chunk is a structural unit of the *source* document (a heading-bounded section), not a pattern applied to already-flattened Markdown; chunk detection belongs at parse time alongside leaf detection, using the same `{start, length}` + name/title key pattern as leaf (§8).
- Chunking is an SRS-level conditional capability: the parser MUST emit chunk entries when it can recover source-document structure; when it can't, no chunk entries are produced for that leaf/document, and callers fall back to leaf-level granularity only (§8).
- Manifest chunk entries need a provenance marker (`source: parser | llm`), distinguishing free/deterministic parser-derived chunks from costly/non-deterministic LLM-derived fallback chunks (§8).
- Summaries should reference chunks, not leaves, as RAPTOR's base clustering layer — including for JATS/HTML, which have only one (trivial) leaf but can still have many parser-derived chunks (§10).
- Chunk span structure stays list-of-spans, but not for the originally suspected reason: `liteparse` already strips header/footer/page-chrome boilerplate before the blob is assembled (`keep_headers_footers=False` default, unoverridden), and tables reconstruct inline, so no known discontiguity source exists for parser-derived chunks — a single `{start, length}` would suffice for every case examined. List-of-spans is kept anyway as a no-cost generalisation (one-element list for the common case), reserved for a real future discontiguity source such as LLM-derived fallback chunks splicing non-adjacent passages (§8).
- Extracted PDF images get a new artefact type alongside `document`/`markdown`, in the same document-hash directory — the Markdown blob only ever carries an inline placeholder/reference, never the raw image bytes, so those need their own storage home (§12).
- **JATS pipeline architecture resolved: no dedicated JATS→Markdown XSLT.** Keep the shared XSLT→HTML→(HTML-to-Markdown backend) pipeline. Swap `markdownify` → `html-to-markdown` (PyPI, MIT, Rust-core prebuilt wheels) — fixes rowspan/colspan for both JATS and arXiv HTML, empirically verified; `markdownify` removed from the project entirely (`pyproject.toml`), and `MarkdownifyHtmlBackend`/`html_markdownify.py` renamed to something library-neutral. Add a narrow XSLT pre-pass converting `mml:*` MathML subtrees to inline LaTeX text before HTML→Markdown handoff (no library tested has native MathML support). Patch the depth-mapping templates to compute heading level dynamically instead of the fixed 3-level enumeration (§13).
- **Summaries/RAPTOR deferred entirely until vector search is designed.** RAPTOR's retrieval mechanisms (collapsed-tree, tree-traversal) are inherently vector/embedding-based with no real keyword-search equivalent; its actual payoff (efficient semantic drill-down, avoiding dumping a whole document's leaves into an LLM's context window) can't be realised until the vector index — already deferred as future work in §2 — exists. `summaries.jsonl`/`generate_full_text_summaries` design work, including §10's zero-chunk-fallback question, is out of scope until then and should be revisited together with vector search's own design, not independently (§14).
- **Manifest table schema resolved.** One table, one row per leaf/chunk/summary entry: `id`, `format`, `kind`, `key`, `provenance`, `scheme`, `spans` (JSON array, not a normalized child table). `ordinal` dropped (span order comes from JSON array order, entry order from sorting by first span's `start`/leaf's numeric `key`). Nesting is derived from span containment among same-`(format, scheme)` chunk entries, not stored (no `parent_id`, no hierarchical key). `provenance` is non-null on every row (not chunk-only): always `parser` for `leaf`, always `llm` for `summary` (provisional), genuinely variable only for `chunk`. `scheme` is free text, not a DB enum, so new chunking schemes don't need a migration. Chunk entries now capture every heading nesting level as separate overlapping rows, superseding §4/§8's earlier flat-chunk framing (§15).

Still not resolved or written into the SRS:

- The concrete per-format chunk-boundary-detection mechanism isn't fully pinned down: a format-agnostic Markdown-heading-walker was proposed for PDF/HTML but not explicitly ratified; JATS's mechanism depends on the now-resolved pipeline (§9, §13).
- The `mml:*` → LaTeX XSLT mapping is only sketched as a concept (trivial superscript case); non-trivial MathML (fractions, integrals, matrices, multi-line equations) needs its own mapping design (§13).
- Whether the depth-mapping fix needs to generalise beyond `<sec>`-based nesting (body matter) to abstract/back-matter nesting, which may not use uniform element names (§13).
- `tests/test_parsers_html_markdownify.py` golden-output assertions need updating for `html-to-markdown`'s output conventions once the swap lands (§13).
- Indices/generated-columns for span-range queries on the manifest table (e.g. supporting `parse_full_text`'s `page` parameter, or "which entries cover byte X") — schema is resolved (§15) but access patterns weren't designed this session.
- Whether `summary` entries will have a real `key` (cluster label) and whether `provenance` is unconditionally `llm` for them — deliberately left open pending summary's own design (§14, §15).
- A concrete `SearchIndex`/indexing-provider interface, separate from `StorageBackend`, with FTS5 as the v1 implementation (from §2).
- Journal mode (WAL vs. rollback) per file type — deferred to implementation.
- Backup/restore functionality for the embedded-DB local backend — deferred as separate future work.
- Whether `liteparse` should be configured with `extract_images=True` at all, given no current tool consumes image bytes — vs. `image_mode="placeholder"`/`"off"` to skip extraction until a concrete tool needs them (§12).
- If image extraction is enabled: catalogue/manifest entries for image artefacts, deletion semantics relative to the `markdown` artefact that references them, and whether search indexing should touch images at all (§12).
- None of `02-storage.md`, `01-architecture.md`, `03-functional-requirements.md`, `06-interface-specification.md`, or `07-test-specification.md` have been edited as part of this debate — this file remains the only artefact produced so far.
