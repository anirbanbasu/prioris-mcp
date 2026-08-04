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

### 16. Chunk-boundary-detection mechanism: format-agnostic Markdown-heading-walker

Resolves the "concrete per-format chunk-boundary-detection mechanism" item from the previous session's "still not resolved" list.

**Ratified: a single, format-agnostic Markdown-heading-walker**, run uniformly over the assembled Markdown blob for PDF, JATS, and HTML alike. Checked empirically rather than assumed: generated a synthetic PDF (via LibreOffice, nested `<h1>/<h2>/<h3>`) and ran it directly through `liteparse` (`output_format="markdown"`) — it correctly reconstructs `#`/`##`/`###`, matching source nesting depth, from its own font/layout heuristics. Combined with JATS (XSLT depth-mapping, §13, patched to compute level dynamically → HTML `h2`-`h4` → `html-to-markdown`) and HTML (DOM headings → `html-to-markdown`, `ATX` style — already pinned for `markdownify` in `html_markdownify.py` and expected to carry over in the library swap), **all three formats converge on genuine ATX heading lines (`#`, `##`, ...) in the final Markdown blob.**

**Algorithm:** scan the blob for ATX heading lines (`^#{1,6}\s+...`), skipping any that fall inside a fenced code block (implementation detail — needed to avoid false positives from literal `#`-prefixed source content, e.g. a shell/Python comment in a reproduced code listing). Each heading's chunk span runs from its own start offset to the start of the next heading whose level is ≤ its own (or end of blob). This directly produces §15's "every nesting level as a separate, overlapping row" shape for free: a shallower heading's span only closes at the next same-or-shallower heading, so a nested subsection's span sits properly contained inside its parent section's span. `key` = heading text; `scheme` = free text per §15 (e.g. `"heading-bounded-v1"`); `provenance` = `"parser"` (a deterministic walk over parser output, not an LLM).

**Refinement to §8's framing:** §8 said chunk detection "belongs at parse time, alongside leaf detection." Still true in spirit (headings are source-document structure, not a pattern applied to already-flattened text), but literally the walk has to run **after** `to_markdown()`, against the rendered blob's own byte offsets — none of the three backends (`liteparse`, the JATS XSLT→HTML pipeline, `trafilatura`+`html-to-markdown`) exposes a source-offset-to-output-offset map; only the final rendered string comes back. "Parse time" should read "Markdown-assembly time."

### 17. MathML: `tex-math`-wins-over-`mml:math` fix, and a from-scratch XSLT mapping for the rest

Investigates the two "not yet designed" MathML items left by §13: the `mml:*` → LaTeX mapping itself, and (found this session, not previously flagged anywhere in the debate) a duplication bug in how the vendored stylesheet handles JATS's `alternatives` wrapper.

**New bug found: `alternatives` renders every child unconditionally, including duplicate math encodings.** JATS formulas from LaTeX-authored papers commonly wrap the same equation twice — once as literal `tex-math`, once as `mml:math` — inside `<alternatives>`. `jats-html.xsl:2399`'s `alternatives` template is `<xsl:apply-templates/>` with no selection logic, so both render. Confirmed end-to-end with a `disp-formula/alternatives` snippet containing `<tex-math>$E=mc^2$</tex-math>` alongside the equivalent `<mml:math>`: both `markdownify` and `html-to-markdown` produce `'[TeX:] $E=mc^2$E=mc2'` — the raw `tex-math` text (with the stylesheet's own `"[TeX:] "` HTML-display marker leaking straight through) directly concatenated with the flattened, superscript-dropped MathML rendering. Worse than §13's isolated-MathML finding (a duplicated, garbled token, not just a lossy one), and independent of the `html-to-markdown` swap — identical in both libraries, since the duplication happens at the XSLT/HTML-generation layer, before either Markdown converter runs.

**Resolved — `tex-math` wins when both alternates are present.** Patch `alternatives` handling (or add a higher-priority template for `disp-formula/alternatives` and `inline-formula/alternatives`) so that when a `tex-math` sibling exists, only it renders — `mml:math` is suppressed entirely — and drop the `"[TeX:] "` prefix (a browser-display hint, meaningless in machine-consumed Markdown). `tex-math` is literal author-provided LaTeX, strictly more faithful than any MathML→LaTeX conversion could be. This also narrows the scope of the general mapping below: it's only needed for formulas that carry MathML with no `tex-math` alternate — real Europe PMC corpus composition (how common that is) wasn't checked this session.

**Resolved — mechanical MathML→LaTeX mapping, implemented as more vendored XSLT, not a new runtime dependency.** Checked whether an existing library could do this rather than designing from scratch: tested `mathml2latex` (PyPI) against fraction, superscript, integral, and matrix MathML. Fraction (`\frac{a}{b}`) and superscript (`E=mc^{2}` — correctly preserving what §13 found `markdownify`/`html-to-markdown` silently flattening) converted correctly; the integral case was structurally correct with looser token choice (`∫` glyph rather than `\int`); the matrix case crashed outright (`mathml tag 'mtr' has not processed'`). This confirms the general approach is mechanically sound (every Presentation MathML element has a direct LaTeX typesetting equivalent — nothing about the rendered mathematics is inherently lossy in this direction, unlike flattening to plain text), but both available PyPI packages (`mathml2latex`, last released Feb 2019; `py-asciimath`, last released Apr 2020) are unmaintained, and the matrix crash is concrete evidence of the kind of bug an abandoned dependency can hand us silently.

**Decision: write the mapping as XSLT templates in the already-vendored stylesheet**, covering the JATS math-element subset already anticipated by the stylesheet's own preserve-space list (`jats-html.xsl:159-160`: `mml:annotation`, `ci`, `cn`, `csymbol`, `mi`, `mn`, `mo`, `ms`, `mtext`) plus the structural containers `mrow`, `mfrac`, `msup`, `msub`, `msubsup`, `msqrt`, `mroot`, `munder`, `mover`, `munderover`, and an `mo`-token → LaTeX-command lookup table (`≤`→`\leq`, `∫`→`\int`, etc., not exhaustive Unicode coverage). This reuses the `lxml.etree.XSLT` engine already in the dependency graph for `jats-html.xsl` — zero new runtime dependency, consistent with this project's established preference (already demonstrated choosing `liteparse`/`html-to-markdown`) for lean, owned code over unmaintained third-party converters.

**`mtable`-as-matrix deliberately deferred, not designed here.** It's the one construct that broke even the reference implementation; rather than guess at the right LaTeX matrix environment upfront, fall back to the current copy-through/flatten behaviour for it until a real Europe PMC document actually needs it.

### 18. Depth-mapping fix generalizes to abstract/back-matter: a three-anchored-counter design

Resolves the "does the depth-mapping fix need to generalise beyond `<sec>`-based nesting" item from the previous session's "still not resolved" list, by reading the actual XPath patterns rather than assuming.

**The vendored stylesheet already covers `abstract` and `back`, not just `body` — but positionally, not by element name.** `jats-html.xsl:1976-2018` gives (reformatted for clarity):

```
main-title:       abstract/title       | body/*/title       | back/title | back[not(title)]/*/title
section-title:    abstract/*/title     | body/*/*/title     | back[title]/*/title | back[not(title)]/*/*/title
subsection-title: abstract/*/*/title   | body/*/*/*/title   | back[title]/*/*/title | back[not(title)]/*/*/*/title
```

These match by structural position (any element sitting where a section would sit), not by element name (`abstract/*/title` matches regardless of what that child element is actually called) — and `back` carries a conditional depth shift: `back[title]` (back has its own direct `<title>`) vs. `back[not(title)]` shifts every subsequent level's mapping by one, because when `back` has no title of its own, its *children's* titles take the "main" slot instead.

**Why `count(ancestor::sec)` (§13's original proposal) would regress this.** It's name-based: `abstract/title`'s parent is `abstract`, never `sec`, so `count(ancestor::sec)` returns 0 there regardless of true depth, colliding with genuine top-level body sections. It also has no equivalent of the `back[title]`-vs-`back[not(title)]` conditional shift at all.

**`<app>` (appendix) is out of scope for this fix regardless.** It's already routed through a separate template (`jats-html.xsl:1928`, `<div class="section app">`) that never touches the title-depth system.

**Design: three separately-anchored dynamic depth counters** (one rooted at `abstract`, one at `body`, one at `back`, replacing the fixed-depth `main-title`/`section-title`/`subsection-title`/generic-`title`-fallback templates with a single generalized template):

For a `title` element T, let R be whichever of `abstract`, `body`, `back` is T's nearest ancestor of that name (in a well-formed single-article JATS document, exactly one matches):

1. **Raw depth** — the number of container elements strictly between R and T:
   `raw-depth = count(T/ancestor::*) - count(T/ancestor::R[1]/ancestor::*) - 1`
   (`R[1]` picks the *nearest* R ancestor — a necessary defensive detail: XPath 1.0 evaluates `[1]` on a reverse axis like `ancestor::` in proximity order, i.e. nearest-to-context-node first, not document order, so `ancestor::body[1]` is "closest enclosing `body`," which is what's wanted here even though only one `body`/`abstract` is expected per article in practice.)
   Sanity check against the existing patterns: `body/*/title` → one intervening element → raw-depth 1; `body/*/*/title` → raw-depth 2; `abstract/title` (T is a direct child of R) → raw-depth 0.

2. **`back`-only shift** — subtract 1 when R is `back` and `back` has no title of its own:
   `back-shift = (R is back) and not(R/title) → 1, else 0`
   (`R/title` here means "R's own direct `<title>` child" — when T itself *is* that title, `R/title` still selects T, so this correctly evaluates to non-empty and the shift stays 0 for `back/title` itself; no separate `parent::back` special case is needed.)

3. **Effective depth:** `depth = raw-depth - back-shift` (0 for `abstract`/`body`; `back` only when it lacks its own title).

4. **Heading level:** `level = min(depth + 2, 6)` — reproduces the existing `depth 0 → h2 (main-title)`, `depth 1 → h3 (section-title)`, `depth 2 → h4 (subsection-title)` mapping, now open-ended instead of falling through to the generic `<h3 class="title">` fallback (§13's confirmed bug) past depth 2, and capped at `h6` since Markdown/HTML have no headings deeper than that.

Implementation shape: one `xsl:template match="title[ancestor::abstract or ancestor::body or ancestor::back]"` computing `raw-depth`/`back-shift`/`depth`/`level` as `xsl:variable`s, then `<xsl:element name="{concat('h', $level)}">` with a `class` of `main-title`/`section-title`/`subsection-title` for `depth` 0/1/2 and `subsection-title` (or a new class) beyond. This template's default XSLT priority (0.5, from the predicate) sits below the existing `block-title` template's explicit `priority="2"` (`jats-html.xsl:2021`, for `list`/`def-list`/`boxed-text`/etc. titles — unaffected by this change) and above the generic `match="title"` fallback's default priority (0, a plain name test) — so priority ordering falls out naturally from XSLT's specificity rules without needing an explicit `priority` attribute, but this ordering should be verified once written, not assumed.

**Related gap, not part of this fix's scope:** `trans-abstract` (translated-abstract) titles are **not** in the matched patterns at all, in the *current* stylesheet, independent of this fix — `jats-html.xsl` only ever names `abstract`, never `trans-abstract`, in the title-depth templates, so a `trans-abstract`'s titles already fall through to the generic fallback today. Pre-existing, not a regression introduced here, but whoever implements this fix shouldn't assume the three-anchor formula is exhaustive over every JATS title-bearing container — it isn't, by the same margin the original code already wasn't.

### 19. `tests/test_parsers_html_markdownify.py`: no golden-output changes needed, but the monkeypatch shape does

Resolves the "golden-output assertions need updating" item from the previous session's "still not resolved" list, by reading the actual test file rather than assuming a snapshot-style test exists.

**The earlier framing was overstated: there are no golden/snapshot assertions in this file at all.** `test_converts_simple_html_to_markdown` (the only test exercising real conversion output) checks three loose substrings — `"# Title" in result`, `"Hello" in result`, `"**world**" in result` — not an exact expected string. Ran the same fixture HTML (`<h1>Title</h1><p>Hello <strong>world</strong>.</p>`) through `html_to_markdown.convert()` directly: output is `'# Title\n\nHello **world**.\n'`, and all three substrings still match, unchanged. Also checked `html_to_markdown.ConversionOptions`'s default `heading_style`: it's `'atx'` already — unlike `markdownify`, which defaults to Setext and needs `html_markdownify.py`'s current explicit `heading_style=ATX` override (`html_markdownify.py:24`) to get ATX. So this test needs no content-level changes at all.

**What genuinely needs to change, found in the other two tests:**

1. **Import path and class name.** `test_slow_parse_raises_parse_error_within_bound` and `test_decode_error_becomes_parse_error` import `MarkdownifyHtmlBackend` from `prioris_mcp.parsers.html_markdownify` — both need updating to whatever name §13 item 5's rename lands on (e.g. `HtmlToMarkdownBackend`/`html_to_markdown_backend.py`).
2. **The monkeypatch target's return shape is a real design fork, not just a renaming exercise.** Both tests monkeypatch the module-level `markdownify` callable with a lambda returning a plain `str` (or raising). But `html_to_markdown.convert(html)` doesn't return `str` — it returns a `ConversionResult`, with the actual text on `.content`. Two ways to structure the new backend module:
   - **(Recommended) Keep a module-level `str -> str` convenience binding**, mirroring today's `functools.partial(_markdownify, heading_style=ATX)` shape — e.g. a small wrapper function that calls `html_to_markdown.convert(html, options=...).content` internally and returns just the string. `to_markdown()` calls this binding, and both tests' existing monkeypatch pattern (replace the module-level name with a plain string-returning/exception-raising lambda) carries over unchanged in structure — only the bound name changes (`markdownify` → whatever the new binding is called).
   - **(Rejected) Call `html_to_markdown.convert` directly inside `to_markdown()`** and extract `.content` at the call site. This would force both tests to construct a fake `ConversionResult`-like stand-in (an object with a `.content` attribute) instead of a bare string, a bigger and less natural test change for no benefit.
3. **Test file name** (`test_parsers_html_markdownify.py`) could be renamed to match (e.g. `test_parsers_html_to_markdown.py`) for consistency with the module rename — a minor housekeeping item, not a correctness requirement.

### 20. Span-range query access patterns: no interval/R-tree indexing needed at this scale

Resolves the "indices/generated-columns for span-range queries" item from the previous session's "still not resolved" list.

**`page_range` (`06-interface-specification.md:21,103,192`) is an already-specified, required output field — "which entries cover byte X" is not a speculative future access pattern.** `research_arxiv_parse_full_text`/`research_localfile_parse_full_text` must return `page_range`, the page(s) an arbitrary `offset`/`limit` window spans, whenever `format="pdf"`. So this item has a concrete, required consumer, not just a hypothetical one.

**Two distinct query shapes, neither needing interval/R-tree indexing:**

1. **`page` parameter → starting offset.** An exact-match lookup: `format`/`kind='leaf'`/`key=<page>`. Verified empirically: a plain B-tree index on `(format, kind, key)` is exactly what SQLite's query planner picks (`EXPLAIN QUERY PLAN` confirmed `SEARCH manifest USING INDEX ... (format=? AND kind=? AND key=?)`).
2. **`page_range` → which page(s) an offset/limit window spans.** Looks like it needs interval-overlap indexing, but doesn't: leaves are contiguous and exhaustive (§3/§4) — no gaps, no overlaps between them — so "which leaf contains offset X" reduces to a sorted binary search over leaf start-offsets. Verified a generated column supports this directly: `span_start INTEGER GENERATED ALWAYS AS (json_extract(spans, '$[0].start')) VIRTUAL`, indexed normally, correctly extracts and sorts on the JSON `spans` array's first start offset (tested against SQLite 3.46.1). This generated column doubles as the index needed for §15's already-agreed "entry order comes from sorting by first span's `start`" rule.

**For `chunk` entries (which *can* overlap, per §15's every-nesting-level decision), true overlap queries are possible but not worth indexing specially.** Manifest files are per-document (§7), holding at most low hundreds of rows — loading all chunk rows for a `(document, format[, scheme])` and computing containment/overlap in application code is cheap at this scale, consistent with this debate's established practice of not over-engineering past the SRS's stated target scale (§7, §14). Confirmed SQLite's R-tree module is available in this project's SQLite build (`3.46.1`, `CREATE VIRTUAL TABLE ... USING rtree(...)` succeeds) if a future access pattern ever needs true interval indexing, but nothing currently justifies adopting it.

**Resolved indexing plan:** a plain B-tree index on `(format, kind, key)` for exact leaf/chunk lookups (including the `page` parameter), plus one generated column `span_start` (extracting `spans[0].start` via JSON1) indexed for sorted/binary-search access (covering both `page_range` and §15's ordering rule). No R-tree, no per-chunk shadow table.

**Dependency, not resolved here:** none of this works correctly until the `ParserBackend`/PDF leaf-span assembly gap (found while resolving §16, still open) is fixed — `page_range` needs leaf spans that are genuinely exhaustive and non-overlapping, which today's `LiteParsePdfBackend.to_markdown()` (returning `result.text` with its fabricated `-----` page separator) breaks.

### 21. `SearchIndex` interface: chunk-based, plain-dict, mirroring `StorageBackend`

Resolves the "concrete `SearchIndex`/indexing-provider interface" item from the previous session's "still not resolved" list (originally raised in §2).

**Interface, mirroring `StorageBackend`'s actual ABC style (`storage.py`):**

```python
class SearchIndex(ABC):
    @abstractmethod
    async def index_entries(self, provider, identifier, format, entries: list[IndexableEntry]) -> None:
        """Replace all indexed entries for (provider, identifier, format) with `entries`."""

    @abstractmethod
    async def remove_document(self, provider, identifier, format) -> None:
        """Remove every indexed entry for (provider, identifier, format)."""

    @abstractmethod
    async def search(self, query, *, provider=None, identifier=None, format=None, limit=...) -> list[SearchMatch]:
        """Full-text search, ranked by relevance (bm25 for the FTS5 v1 implementation)."""
```

**`index_entries` is whole-document replace, not incremental upsert.** A parse pass produces a document's complete manifest fresh each time, so there's nothing to diff against — matches §2's "pure, disposable cache" framing, and keeps the interface to three methods.

**Indexed unit is a manifest entry (chunk, or leaf as fallback), not the whole blob.** Corrects a gap found this session: `research_search_fetched`'s existing SRS text (`06-interface-specification.md`) says it searches "over persisted `markdown` artefacts" — whole-document blobs — which predates and contradicts §2's already-agreed chunk-based indexing decision. The zero-chunk leaf fallback here is not a new decision; it's §8's already-agreed general principle ("callers needing sub-leaf granularity fall back to leaf-level only") applied to the search index as one instance of such a caller — distinct from §10's still-open, still-deferred question of whether *summary*'s base layer falls back to leaves, which stays out of scope until vector search is designed (§14).

**Verified empirically against SQLite 3.46.1's FTS5:**

- `UNINDEXED` columns (`provider`, `identifier`, `format`, `entry_key`, `span_start`) carry metadata alongside the tokenized `text` column without being searched themselves — confirmed via `CREATE VIRTUAL TABLE search USING fts5(provider UNINDEXED, identifier UNINDEXED, format UNINDEXED, entry_key UNINDEXED, span_start UNINDEXED, text)`.
- `snippet()`/`bm25()` work as expected for ranking and highlighting.
- Deletion is metadata-scoped, not rowid-based: `DELETE FROM search WHERE provider=? AND identifier=? AND format=?` works directly, matching `research_delete_fetched`'s per-artefact scope. This also sidesteps a real hazard: manifest entry `id`s are only unique per-document (§7's per-document manifest files), so they aren't safe to reuse as FTS5 rowids across the shared `search.sqlite3` file — auto-assigned rowids avoid the collision entirely.

**`offset` in `research_search_fetched`'s output should mean the matched entry's `span_start`, not an FTS5-internal offset.** FTS5's own `offsets()`/`snippet()` are relative to the indexed `text` column itself (confirmed empirically), not the document blob's coordinate space. Denormalizing `span_start` as its own `UNINDEXED` column sidesteps needing FTS5's internal offset machinery, and lets a caller re-fetch context around a hit via `parse_full_text`'s existing `page`/`offset` parameters.

**Overlapping hits are an accepted characteristic, not a defect.** §15's "every heading nesting level as its own row" means a query can legitimately match both a broad section chunk and its nested subsection chunk for the same underlying text. `bm25` naturally favors the more topically-dense (usually narrower) chunk, but overlapping results should be expected in the output, not treated as an indexing bug.

**`IndexableEntry`/`SearchMatch` are plain `dict`s, not Pydantic models.** Checked the actual codebase convention rather than assuming: `StorageBackend` (`storage.py`) — the interface `SearchIndex` is explicitly designed to mirror — returns bare `dict`/`list[dict]` throughout (e.g. `list()` → `list[dict]`, `read_manifest()` → `dict | None`), no Pydantic anywhere. Pydantic `BaseModel` is reserved in this codebase for the MCP tool/resource wire boundary specifically (`06-interface-specification.md`'s own stated convention; `providers/base.py`'s methods return `BaseModel` because they feed tool outputs directly). `SearchIndex` sits at the same internal-backend layer as `StorageBackend`, not the tool boundary — the actual Pydantic model is `research_search_fetched`'s own already-specified output, built *from* `SearchIndex.search()`'s result, not identical to it. Introducing Pydantic (or `TypedDict`) for `SearchIndex` alone, while `StorageBackend` stays untyped `dict`, would create two competing internal-interface conventions side by side — deliberately not done here; tracked as its own future enhancement (adopting `TypedDict` across internal backend interfaces generally, `StorageBackend` included) rather than scoped into this design.

### 22. PDF image extraction resolved: off by default, `PRIORIS_MCP_PDF_EXTRACT_IMAGES`, full artefact/resource pipeline when enabled

Resolves §12's two "not resolved" bullets.

**Scope check first: PDF-specific, not a general "image extraction" toggle.** JATS/HTML images are link references only (`graphic`/`inline-graphic`'s `@xlink:href`, or plain `<img src>`) — §13 already established JATS parsing has no image bytes available at parse time at all, and `html_markdownify`'s pipeline never resolves or fetches linked images either. There is no "extraction" concept for those formats; this is exclusively about `liteparse`'s embedded raster images from PDFs.

**Off by default, via a new env var, not a hardcoded `False`.** New `EnvVars.PRIORIS_MCP_PDF_EXTRACT_IMAGES: bool`, default `False` — named to match the existing `PRIORIS_MCP_PDF_OCR_*` family (`__init__.py:147-170`) rather than a generic `PRIORIS_MCP_EXTRACT_IMAGES`, since this is a LiteParse/PDF-backend-specific toggle, the same way the OCR settings are. `LiteParsePdfBackend` passes it straight through to `LiteParse(extract_images=...)`; `image_mode` stays `"placeholder"` regardless of the toggle, so Markdown rendering (inline image refs) is unaffected either way. When `False`: no image bytes pulled, no `kind="image"` manifest entries, no image resources registered — behaviourally identical to today.

**When enabled, the full pipeline is concrete, not just conceptual — checked against `liteparse/types.py` and FastMCP's actual resource API rather than assumed:**

- `ExtractedImage.bytes` (`types.py:203`) gives raw image bytes directly — no `image_output_dir`/disk round-trip needed. `ExtractedImage.id` (`types.py:194`) matches the Markdown reference's `id` (`![](img_p1_1.png)` → `id="p1_1"`), the same anchor role spans play for leaf/chunk text — this is what ties an image artefact back to its position in the blob.
- Image bytes are persisted via the existing `StorageBackend.write()` — just another `format` value (e.g. `"image/png"`) keyed by (provider, identifier+image-id) — no interface change to `StorageBackend` needed. This supersedes §12's earlier placeholder path description (`documents/<document-hash>/pdf/images/<image-id>`), which predated confirming `FilesystemStorageBackend`'s actual flat storage-key-hashed layout (`storage.py:200-204` — no nested `documents/<hash>/...` directory structure exists).
- New manifest entry `kind`: `"image"`, alongside `leaf`/`chunk`/`summary` from §15's schema, carrying `page`, `bbox`, and `duplicate_of` (liteparse's own bytes-level dedup, `types.py:204` — a separate concern from `StorageBackend`'s content-addressing).
- New MCP resource per image, not a new architectural pattern — FastMCP has a native `BinaryResource` type and a `mime_type` field on `Resource` (confirmed via `fastmcp.resources`), and `mixin.py`'s `resources` list already forwards arbitrary kwargs straight to `mcp.resource()`, so this is one more declarative entry, not a new subsystem. URI scheme still needs to be picked (e.g. `research://{provider}/{identifier}/images/{image_id}`), consistent with `05-security.md`'s "no server-side reads by path" constraint — the URI/handler must not leak raw filesystem paths.
- `research_delete_fetched` needs to cascade-delete image artefacts tied to a document; currently it only targets one (provider, identifier, format) triple at a time.
- **Explicitly still out of scope:** search indexing over images. No OCR happens here, so images aren't text-searchable content — they stay linked artefacts, not indexed `SearchIndex` entries.

### 23. `ParserBackend`/PDF leaf-span assembly gap resolved: `to_markdown()` returns a plain dict, not a bare string

Resolves the gap found while resolving §16, listed in §12/§20's "still not resolved" notes.

**Checked the actual codebase first: no manifest-builder code exists yet at all** — the three real call sites of `to_markdown()` (`europepmc.py:225`, `arxiv.py:325`, `localfile.py:303`) currently do `markdown = await backend.to_markdown(source_content)` and pass the bare string straight to `StorageBackend.write()`. This is genuinely greenfield; the fix only has to satisfy the ABC signature and these three call sites, not an existing manifest consumer.

**Interface change:** `ParserBackend.to_markdown(content: bytes) -> str` becomes `-> dict`, returning `{"markdown": str, "leaf_spans": list[dict]}`, each span `{"start": int, "length": int}` — the same shape §15 already gives manifest spans. Plain `dict`, not a dataclass or Pydantic model: matches §21's established convention for internal (non-wire) interfaces (`StorageBackend`/`SearchIndex` precedent), and there is no dataclass precedent anywhere in this codebase to justify introducing one here (checked — every class in `parsers/`/`models/` is either a `ParserBackend` subclass or a Pydantic `BaseModel`, nothing in between).

**Per-backend behaviour:**

- `LiteParsePdfBackend`: stops using `result.text` entirely (§16/§20's finding: it inserts a fabricated `\n\n-----\n\n` separator between pages absent from either page's real content, and `-----` collides with genuine Markdown horizontal-rule syntax, so it isn't safely stripped after the fact). Instead builds the blob directly from `result.pages[i].markdown`, joined with an explicit joiner of our own choosing (e.g. `"\n\n"` — ordinary Markdown block separation, never mistakable for fabricated content), tracking each page's `{start, length}` while joining. Fully decouples the leaf-span computation from `liteparse`'s internal display convention.
- `JatsXsltMarkdownBackend`/`MarkdownifyHtmlBackend`: trivial single-leaf case, `{"markdown": md, "leaf_spans": [{"start": 0, "length": len(md)}]}` — makes §3's "JATS/HTML have only one leaf" finding an explicit interface guarantee rather than an assertion that lived only in this debate doc.
- Chunk spans are explicitly *not* part of this return value — §16 already established chunk-boundary detection runs downstream, format-agnostically, over the assembled blob (the Markdown-heading-walker). Only leaf structure needs to come from the backend, since it's source-format-specific (PDF pages) and unrecoverable from the blob alone once joined.

**`markdown` stays plain `str`, no base64/encoding layer.** Considered and rejected: base64-encoding the Markdown blob to protect against it "messing up JSON." Traced where JSON actually touches this data and found the concern doesn't apply here:

- The `to_markdown()` return dict itself is never JSON-serialized — it's an in-memory Python dict passed directly between async calls within the same process (parser backend → provider → storage write), not persisted or transmitted as JSON at that point.
- `StorageBackend.write()` (`storage.py:209-232`) writes the Markdown blob as raw bytes to a `.data` file; the separate `.json` manifest file holds only metadata (`provider`, `canonical_identifier`, `format`, `size_bytes`, `fetched_at`), never the content itself.
- The manifest's `spans` column (§15) is JSON, but only ever holds the small `{start, length}` integer dicts, never text.
- The one place Markdown text does cross a real JSON boundary — the MCP wire response (`ParsedFullText.markdown: str`, already shipping) — was verified empirically rather than assumed: nasty Markdown (quotes, backslashes, code fences, unicode, tabs) round-trips losslessly through both `json.dumps`/`json.loads` and Pydantic's `model_dump_json`/`model_validate_json`, because standard JSON string escaping handles arbitrary text correctly by construction.
- Base64 would add ~33% storage bloat and an encode/decode step for no benefit, and would still need decoding back to `str` before leaf-span computation anyway, since spans are string character offsets — the same point the user raised independently.

**Mechanical fallout, not itself a design question:** the three call sites need `result["markdown"]`/`result["leaf_spans"]` instead of a bare string; several test fakes in `tests/test_parsers_*.py`/`tests/test_providers_*.py` implement `ParserBackend` directly (`async def to_markdown(self, content: bytes) -> str`) and need their signatures/return values updated to match.

### 24. MathML residuals resolved: `mtable`→LaTeX design grounded in real corpus data, mo-token lookup sourced from the MathML Core operator dictionary, corpus composition checked empirically

Resolves §17's three deferred items ("not yet designed": `mtable`-as-matrix, multi-line/multi-row equations, `mo`-token lookup table, real corpus composition).

**Corpus composition checked against live data, not assumed.** Queried Europe PMC's REST API for 30 open-access articles likely to contain formulas (`equation OR formula` combined with `physics OR mathematics OR mathematical`), fetched each article's full-text JATS XML, and counted `<tex-math` / `<mml:math` occurrences directly. Of the 16 articles containing any math markup: 1 was dual-encoded (`PMC13290966` — 516 `<alternatives><tex-math>...<mml:math>...</alternatives>` pairs, a one-to-one match confirming exactly the wrapper pattern §17 already fixed), 5 were `tex-math`-only, and **10 (63%) were `mml:math`-only, with no `tex-math` fallback present at all**. Small sample (30 articles), so this is directional rather than a definitive population statistic, but it reframes priority: the `mml:*`→LaTeX mapping is the majority path for math-bearing articles in this sample, not a rare fallback behind `tex-math`.

**`mtable` usage inspected directly in the sampled XML rather than designed from the MathML spec alone.** Three real patterns turned up, not the single "matrix" case originally assumed:

- **Single-cell wrapper** (1 row, 1 column, e.g. `PMC13050331`'s `id="d33e709"`): a single formula wrapped in `mtable` purely to carry `columnalign` display metadata, an artifact of the upstream LaTeX→XML conversion tooling that produced the JATS source. No genuine alignment/stacking semantics — collapses to just the inner cell's content, no LaTeX array environment at all.
- **Piecewise/cases**: `mtable` wrapped in `<mml:mfenced separators="" open="{" close="">` (confirmed verbatim in `PMC13258359` — a 2-row, 2-column piecewise function definition). Maps directly to LaTeX's `cases` environment, which is itself open-brace-only with no closing delimiter — the MathML idiom mirrors `cases` exactly, not a coincidence.
- **Multi-row aligned derivation** (`PMC13050331`'s `id="d33e1150"`): multiple `<mtr>`, `columnalign="right"`, with an `mtable` nested inside one `<mtd>` of an outer `mtable`. This *is* the "multi-line/multi-row equations" item from §17's list — not a separate mechanism, the same construct as plain `mtable` handling with more than one row and no fence wrapper.

**Design: one recursive `mtable` handler, not a separate matrix path plus a separate multi-line path.** Check for a fence wrapper first:

| Fence (`mfenced`/explicit fence `mo`s) | LaTeX environment |
|---|---|
| `(` / `)` | `pmatrix` |
| `[` / `]` | `bmatrix` |
| `\vert` / `\vert` | `vmatrix` |
| `{` / *(none)* | `cases` |
| none, 1 row × 1 column | *(no wrapper — emit the cell's content directly)* |
| none, multiple rows | `aligned` / `gathered` |

Each `<mtr>` becomes one LaTeX row; each `<mtd>`'s content is converted recursively through the same element-to-LaTeX templates already designed in §17 (fractions, sub/superscripts, roots, under/over) and this table itself — handling the nested-`mtable`-inside-`mtd` case (`d33e1150`) for free, since cell content just recurses through the same template dispatch rather than needing bespoke nesting logic. No bracket-delimited numeric-matrix example turned up in this particular sample, but the `(`/`)`, `[`/`]`, `|`/`|` rows above cover it the same way `cases` was derived from the piecewise example actually found.

**`mo`-token → LaTeX lookup table: sourced from a canonical reference, scoped to observed usage rather than attempting full coverage.** Build the table from the W3C MathML Core Operator Dictionary (the canonical, publicly documented character→semantics mapping) rather than inventing entries ad hoc, but don't gate this on enumerating its full few-hundred-entry set before shipping — scope the initial table to the ~40-60 operators actually common in STEM content (comparison operators, arrows, set operators, Greek letters, calculus symbols). Unmapped `mo` tokens fall back to passing the literal Unicode character through inside the LaTeX span (`$...$`) rather than erroring or blocking — graceful degradation, consistent with how `tex-math`-wins (§17) and the leaf-fallback principle (§8) already treat "no complete answer available" elsewhere in this design.

### 25. `trans-abstract` heading depth: deferred to future multi-language document support, not folded into §18

Follow-up to §18's flagged gap (`trans-abstract` titles aren't covered by any of the three-anchor depth-mapping templates — pre-existing, unaffected by §18's fix). Checked whether folding in a fourth anchor (`trans-abstract`) would be a clean addition, rather than assuming either way.

**Checked against the actual stylesheet (`jats-html.xsl`), not assumed:**

- `trans-abstract`'s content is dispatched through ordinary `apply-templates` (the front-matter abstract-rendering block: `apply-templates select="*[not(self::title)]"`), so a `<sec>` nested inside a `trans-abstract` would flow through the same title-matching machinery as `abstract/sec`. Extending §18's raw-depth formula's anchor set to `{abstract, body, back, trans-abstract}` would mechanically fix nested-title depth for `trans-abstract` exactly the way it already does for `abstract` — no additional `back`-style conditional shift needed, since nothing suggests `trans-abstract` has an equivalent quirk.
- **The top-level `trans-abstract/title` case is likely moot regardless.** The same front-matter block extracts the top-level `abstract`/`trans-abstract` title directly via `apply-templates select="title/node()"` and hardcodes it into an `<h4 class="callout-title">` (with a "Translated " label for `trans-abstract`) — bypassing the depth-mapping templates entirely. `main-title`'s existing `abstract/title` match arm is therefore likely already dead code for this rendering path; a symmetric `trans-abstract/title` arm would be equally inert.

**Decision: defer, don't fold in.** The "clean fix" above rests on an unverified assumption — that a `trans-abstract` never contains nested `<sec>` structure in practice. Unlike §24's `mtable` design (grounded in sampling 30 real Europe PMC articles) or §13/§19's library-swap decisions (tested against real fixtures), nobody has checked whether any real Europe PMC article's `trans-abstract` actually carries nested sections. Fixing the heading-depth corner in isolation, without knowing whether that shape of document exists, would also only be solving a fragment of a larger not-yet-designed question: whether/how this project supports multi-language documents at all (separate manifests per language? one blob covering multiple abstracts? chunking/search implications of a document having two abstracts?). `trans-abstract` heading depth is deferred as part of that larger, currently out-of-scope multi-language-support question, not fixed piecemeal now.

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
- **Chunk-boundary-detection mechanism ratified: a single format-agnostic Markdown-heading-walker**, run over the assembled blob at Markdown-assembly time (not literally at source-parse time as §8 originally framed it) for PDF, JATS, and HTML alike — empirically confirmed `liteparse` reconstructs nested ATX headings faithfully from font/layout heuristics, matching JATS's and HTML's own convergence on ATX via `html-to-markdown`. Each heading's chunk span runs to the next heading of level ≤ its own, which produces §15's every-nesting-level-as-a-row shape as a natural consequence (§16).
- **Depth-mapping fix's abstract/back-matter generalization resolved.** The vendored stylesheet already covers `abstract` and `back` positionally, not just `body` (`jats-html.xsl:1976-2018`), including a conditional depth shift for whether `back` has its own title. §13's originally-proposed `count(ancestor::sec)` fix would have regressed this (name-based, no equivalent of the `back` shift). Designed as three separately-anchored dynamic depth counters instead — one each for `abstract`/`body`/`back` — collapsing the fixed 3-level templates into one generalized template. `<app>` (appendix) is unaffected (separate template, never used the title-depth system). `trans-abstract` remains uncovered, but that's a pre-existing gap, not a regression from this fix (§18).
- **MathML resolved on two fronts.** A new duplication bug found and fixed: JATS `alternatives` wrappers containing both `tex-math` and `mml:math` for the same formula currently render both (confirmed producing a garbled, duplicated token in both `markdownify` and `html-to-markdown`, independent of the §13 library swap) — patched so `tex-math` wins outright when present, since it's literal author-provided LaTeX. For MathML-only formulas, the `mml:*` → LaTeX mapping will be written as more vendored XSLT (reusing the existing `jats-html.xsl`/`lxml.etree.XSLT` machinery), not an unmaintained third-party package — both PyPI candidates tested (`mathml2latex`, `py-asciimath`) are years-abandoned, and `mathml2latex` crashed outright on a matrix case, though it correctly handled fractions/superscripts/integrals, confirming the mechanical element-to-LaTeX mapping approach itself is sound. `mtable`-as-matrix support deliberately deferred (§17).
- **`tests/test_parsers_html_markdownify.py` resolved: no content-level changes needed, only naming and one design choice.** The earlier "golden-output assertions" framing was overstated — the file has no exact-match/snapshot assertions, only loose substrings, and those already match `html-to-markdown`'s output verbatim (confirmed empirically); `html-to-markdown`'s `heading_style` also defaults to `atx` already, unlike `markdownify`. What actually needs to change: the import/class name (per §13 item 5's rename), and the two error-path tests' monkeypatch target — recommended fix is to keep a module-level `str -> str` convenience binding (extracting `.content` from `html_to_markdown.convert()`'s `ConversionResult` internally) so the existing monkeypatch-a-plain-callable test pattern carries over unchanged except for the name (§19).
- **`SearchIndex` interface resolved: chunk-based (leaf-fallback per §8, not §10's still-deferred summary case), plain-`dict`, mirroring `StorageBackend`.** Three methods (`index_entries` whole-document-replace, `remove_document`, `search`). Corrects `research_search_fetched`'s existing SRS text, which said indexing runs over whole `markdown` artefacts — predates and contradicts §2's chunk-based decision. `offset` in search results should be the matched entry's `span_start`, not an FTS5-internal offset. FTS5 mechanics (`UNINDEXED` metadata columns, `snippet()`/`bm25()`, metadata-scoped deletion) verified empirically. `IndexableEntry`/`SearchMatch` are plain dicts, matching `StorageBackend`'s actual convention, not Pydantic — Pydantic stays reserved for the MCP wire boundary (§21).
- **Span-range query access patterns resolved: no interval/R-tree indexing needed at this scale.** `page_range` is an already-required output field, not speculative, so this had a concrete consumer. `page` parameter lookup is a plain B-tree index on `(format, kind, key)`. `page_range` reduces to sorted binary search over a generated `span_start` column (`json_extract(spans, '$[0].start')`, verified working and indexable in SQLite 3.46), exploiting that leaves are contiguous/exhaustive — no true interval query needed. Chunk overlap queries (chunks can overlap, unlike leaves) stay as load-all-rows-for-a-document-and-filter-in-application-code, since manifest files hold at most low hundreds of rows per document (§7) — R-tree confirmed available in this project's SQLite build but not justified at this scale. Depends on the still-open `ParserBackend`/PDF leaf-span gap being fixed first (§20).
- **PDF image extraction resolved: off by default via new `PRIORIS_MCP_PDF_EXTRACT_IMAGES` env var (matching the `PRIORIS_MCP_PDF_OCR_*` naming precedent), full pipeline designed for when enabled.** PDF-specific — JATS/HTML images are link references only, never embedded bytes at parse time. When enabled: `ExtractedImage.bytes`/`.id` give bytes and a blob-position anchor directly; images persist via the existing `StorageBackend.write()` (no interface change); new manifest `kind="image"` entry (`page`, `bbox`, `duplicate_of`); new declarative MCP resource per image via FastMCP's `BinaryResource`/`mime_type` (not a new subsystem), URI scheme still TBD but must not leak filesystem paths (`05-security.md`); `research_delete_fetched` needs a cascade-delete for image artefacts. Search indexing over images stays explicitly out of scope (no OCR) (§22).
- **`ParserBackend`/PDF leaf-span assembly gap resolved: `to_markdown()` returns a plain dict, not a bare string.** `ParserBackend.to_markdown(content: bytes) -> str` becomes `-> dict`, returning `{"markdown": str, "leaf_spans": list[dict]}` (each span `{"start", "length"}`, matching §15's manifest span shape) — plain dict per §21's internal-interface convention, no dataclass (no precedent for one anywhere in this codebase) and no Pydantic (reserved for the wire boundary). `LiteParsePdfBackend` stops using `result.text` (its fabricated `-----` page separator) and instead builds the blob from `result.pages[i].markdown` directly with its own joiner, tracking `{start, length}` per page. JATS/HTML backends return the trivial single-leaf case. Chunk spans stay out of this return value (computed downstream per §16). Considered and rejected base64-encoding `markdown` to protect JSON: traced every point JSON touches this data and found the blob itself is never JSON-serialized in the storage path (raw bytes to a `.data` file; the manifest's `spans` JSON column only ever holds span integers); the one real JSON boundary (`ParsedFullText.markdown` in MCP tool responses) was verified empirically to round-trip nasty Markdown (quotes, backslashes, code fences, unicode) losslessly via standard JSON string escaping. Three real call sites (`europepmc.py:225`, `arxiv.py:325`, `localfile.py:303`) and several test fakes need mechanical updates to match the new return shape (§23).
- **MathML residuals resolved: `mtable`→LaTeX design, `mo`-token lookup strategy, and corpus composition, all grounded in live Europe PMC data rather than assumption.** Sampled 30 open-access articles via Europe PMC's REST API; of the 16 containing math markup, only 1 was dual-encoded (exactly the `<alternatives>` pattern §17 already fixed), 5 were `tex-math`-only, and 10 (63%) were `mml:math`-only — small sample, directional not definitive, but the `mml:*`→LaTeX mapping is the majority path here, not a rare fallback. Real `mtable` usage inspected directly turned up three patterns, not one: a single-cell display-metadata wrapper (collapses to bare cell content), a piecewise/`cases` pattern (`mfenced open="{" close=""`, confirmed verbatim), and genuine multi-row aligned derivations with nested `mtable`s — the last of these *is* the "multi-line/multi-row equations" item, not a separate mechanism. Designed as one recursive `mtable` handler keyed on fence delimiters (`(`/`)`→`pmatrix`, `[`/`]`→`bmatrix`, `|`/`|`→`vmatrix`, `{`/none→`cases`, no fence+1×1→bare content, no fence+multiple rows→`aligned`/`gathered`), with cell content converted recursively through the same §17 element templates, handling nesting for free. `mo`-token lookup table sourced from the W3C MathML Core Operator Dictionary, scoped to ~40-60 commonly-observed operators rather than full coverage, with unmapped tokens falling back to literal Unicode pass-through (§24).
- **`trans-abstract` heading depth deferred until multi-language document support is designed, not folded into §18's fix.** Checked against the actual stylesheet: extending §18's anchor set to include `trans-abstract` would be mechanically clean for nested titles (same `apply-templates` dispatch path as `abstract`), and the top-level-title case is likely moot either way (already bypasses the depth templates via a hardcoded `callout-title` block). Deferred anyway because the fix's cleanliness rests on an unverified assumption — that `trans-abstract` never contains nested `<sec>` structure in real Europe PMC XML — and fixing this corner alone would only address a fragment of the larger, not-yet-designed question of whether/how this project supports multi-language documents at all (§25).

Still not resolved or written into the SRS:

- Whether `summary` entries will have a real `key` (cluster label) and whether `provenance` is unconditionally `llm` for them — deliberately left open pending summary's own design (§14, §15).
- None of `02-storage.md`, `01-architecture.md`, `03-functional-requirements.md`, `06-interface-specification.md`, or `07-test-specification.md` have been edited as part of this debate — this file remains the only artefact produced so far.

Deferred to implementation time (bounded by constraints already agreed above, not open design questions):

- Journal mode (WAL vs. rollback) per file type.
- Backup/restore functionality for the embedded-DB local backend.
- The image resource URI scheme's exact shape (e.g. `research://{provider}/{identifier}/images/{image_id}`) and writing up `research_delete_fetched`'s image-cascade-delete as a concrete interface change — bounded by `05-security.md`'s no-filesystem-path-leakage constraint (§22).
