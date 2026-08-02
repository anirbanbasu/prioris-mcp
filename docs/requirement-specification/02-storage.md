---
icon: lucide/hard-drive
---

# Storage

## Purpose

`fetch_full_text` and `parse_full_text` are the two capabilities in [`ResearchPublicationProvider`](01-architecture.md) that produce content requiring persistence. `search`, `list_top_n`, and `fetch_metadata` results are small and already covered by the server's response-caching middleware. Full text (a PDF, an HTML document, ...) and its parsed Markdown are different: potentially large (full text) or expensive to produce (parsing is CPU-heavy), so a repeat request for either shouldn't need to redo the work if it doesn't have to. The `StorageBackend` abstraction is where both are persisted.

## `StorageBackend`

### Interface

| Operation | Description |
|---|---|
| `exists` | Whether a given item, in a given format, has already been persisted. |
| `write` | Persist content for a given item/format/artefact; returns a location/reference. |
| `read` | Retrieve previously persisted content for a given item/format/artefact. |
| `list` | Enumerate persisted catalogue entries, optionally filtered by provider/format. |
| `delete` | Remove a persisted item/format/artefact (and its catalogue entry) if present. |
| `search` | Full-text search over persisted, parsed Markdown, optionally scoped to one provider/identifier/format. See [Full-text search](#full-text-search-fts5-index) below. |

`fetch_full_text` checks `exists` before performing a network fetch, and can return the already-persisted copy instead of downloading again. This is the mechanism that avoids redundant downloads for full text — it lives in the storage abstraction itself, not in the server's generic response-caching middleware, which is a poor fit for potentially large binary documents.

`list` and `delete` back the grouping-level `research_list_fetched`/`research_delete_fetched` MCP tools (see [Architecture → `list_fetched`/`delete_fetched`](01-architecture.md#list_fetched-delete_fetched-grouping-level)) — a caller-driven way to enumerate or remove specific entries (e.g. correcting a mistakenly-fetched wrong identifier), distinct from the deferred, disk-pressure-driven [retention/eviction](#future-retention-and-redistribution-aware-persistence) policy below. Both operate on whichever catalogue entries already exist, for any provider, without touching content that isn't already persisted.

`parse_full_text` follows the same pattern one level up: it checks `exists` for the *parsed* Markdown first and returns it if present; only if that's missing does it check `exists` for the source full text, parse it (CPU-heavy), `write` the Markdown, and return it. It never triggers a `fetch_full_text` itself (see [Architecture](01-architecture.md)) — if the source full text isn't there either, it fails with a "not found" error.

Raw full text and its parsed Markdown are now two **artefacts** of the same `(provider, canonical identifier, format)` entry — `document` and `markdown` respectively — rather than two independent format values (see [Directory layout](#directory-layout) below). This reuses the existing `exists`/`write`/`read` contract, parameterised by an additional `artefact` argument, rather than introducing a second, parallel concept for derived content. Because canonical identifiers are immutable once resolved (see [Identifier canonicalisation](#identifier-canonicalisation)), a persisted parse result is exactly as permanently valid as the source content it was derived from — no separate invalidation logic is needed for it.

### Identity and location

Persisted content is identified by **provider + canonical item identifier + format + artefact** (e.g. arXiv item `2601.05525v2`, format `pdf`, artefact `markdown`), not by a filename the caller chooses. This keeps `exists`/`write`/`read` consistent regardless of which backend is in use, and avoids collisions between providers that might otherwise reuse the same identifier scheme. "Canonical" is doing real work here — see [Identifier canonicalisation](#identifier-canonicalisation) below.

#### Storage keys are hashed, not built from the raw identifier

An identifier is not safe to use directly as a filename or path segment: DOIs contain `/` (e.g. `10.1000/xyz123`), identifiers can contain arbitrary characters, and filenames built from external input are a path-traversal risk if anything ever produces a malformed or adversarial-looking identifier. Enumerating and escaping every unsafe character is easy to get subtly wrong; filename-length limits are a further, separate constraint.

The top-level storage key — the **document-hash** — is derived by **hashing** `(provider, canonical identifier)` — e.g. a SHA-256 hex digest — rather than encoding the identifier into the path. `format` is deliberately *not* part of this hash: it's a plain, literal path segment (`pdf`, `html`, ...) nested under the document-hash directory, so every format fetched for the same document lands under one shared parent rather than at unrelated, independently-hashed locations (see [Directory layout](#directory-layout)). `format` doesn't need its own hashing — it's drawn from a small, fixed, already filesystem-safe vocabulary, unlike an externally-supplied identifier. The document-hash itself is always a fixed-length, filesystem-safe string, which removes the character-safety and path-traversal concerns entirely rather than mitigating them case by case.

Human-readability is preserved separately: each format directory has a small `metadata.jsonl` (provider, original identifier, canonical identifier, format, fetch/parse timestamps, ...) rather than a descriptive filename, and the top-level `catalogue.jsonl` (see [The catalogue](#the-catalogue-cataloguejsonl)) indexes every entry across the whole store. This also gives future eviction/inspection tooling a natural place to look.

### Directory layout

```
$XDG_DATA_HOME/prioris-mcp/downloads/
  documents/
    <document-hash>/                 sha256([provider, canonical identifier])
      summaries.jsonl                 reserved — see Future below; not produced by v1
      pdf/
        document                      raw fetched bytes
        markdown                      parsed Markdown (artefact "markdown")
        structure.jsonl                native document structure — see below
        metadata.jsonl                 per-artefact fetch/parse timestamps, sizes, ...
      html/
        document
        markdown
        metadata.jsonl                 no structure.jsonl — HTML has no page concept
  catalogue.jsonl
  search.sqlite3                       disposable FTS5 index — see below
```

Every format a caller fetches for the same document (e.g. arXiv exposing both `pdf` and `html` full text for the same item) gets its own subdirectory under the same `<document-hash>`, since `document`/`markdown`/`structure.jsonl` genuinely differ per format — a PDF's page structure has no HTML equivalent, and the two parses can legitimately disagree on wording (OCR artefacts, layout reconstruction) even for the same underlying publication.

#### `structure.jsonl`: native structure, never generated, never duplicated text

`structure.jsonl` records **pointers into `markdown`** — `{start, length}` character ranges — never a copy of the text itself; duplicating parsed Markdown inside its own sidecar would be exactly the kind of drift-prone redundancy the rest of this design avoids. It exists only for `format = pdf` (HTML/JATS have no native page concept, so the file is simply absent for those formats), and its content is always **deterministic and extracted, never generated** — produced synchronously as part of `parse_full_text`, with no LLM involvement and no dependency on [`generate_full_text_summaries`](#future-document-level-generated-content) below.

At minimum it is a flat list of page boundaries: one entry per PDF page, `{"page": <int>, "start": <int>, "length": <int>}`, built by joining `liteparse`'s per-page `ParsedPage.markdown` output (`LiteParse.parse()` → `ParseResult.pages[i].markdown`) into the single `markdown` artefact and recording each page's resulting offset range as it's joined — never parsed back out of a separator, which would be fragile if a page's own content ever contained it.

When the source PDF is tagged (common for LaTeX/`hyperref`-generated academic PDFs, essentially never present for OCR'd content — see [Security → OCR language data](05-security.md#ocr-language-data-is-a-network-dependency-of-parse_full_text)), `structure.jsonl` becomes a genuine tree: `liteparse` exposes a page-scoped tagged-PDF logical structure tree (`ParsedPage.structure_tree`, populated when parsing with `extract_structure_tree=True`; `StructureTreeElement` carries `element_type`, `title`/`actual_text`, and nested `children`). Each heading-like element is resolved to a `{start, length}` range by locating its `actual_text`/`title` within its own page's already-known slice of `markdown`, and nested under its parent per the tree's own `children`. If a heading's text can't be confidently located (OCR noise, whitespace/hyphenation reflow), that boundary is dropped rather than guessed at — the safe default is an incomplete tree, not a wrong one. When no tagged structure is present at all, `structure.jsonl` degrades gracefully to the flat page-only list above; no functionality is lost, the richer tree is purely additive when the signal exists. `liteparse>=2.9.0` (the project's existing minimum pin) already includes `extract_structure_tree` support — first shipped in `liteparse` 2.8.1 — so no dependency version bump is needed for this.

### The catalogue: `catalogue.jsonl`

A single, top-level, append-only index — one JSON line per catalogue write/delete event (or a periodically compacted snapshot) — rather than requiring `list`/`exists`/`find_canonical_identifier` to read every format directory's own `metadata.jsonl` individually. It is the sole source those three operations read from; `write`/`delete` are the only two operations that mutate it, mirroring how they're already the only two operations that touch `metadata.jsonl` today. Diffable, greppable, human-readable, no schema migrations, and fast to replay into an in-memory dict at process startup for O(1) lookups on the hot path — preferred over a database as the *source of truth* at the storage tier's target scale (tens of thousands of entries, not millions), and because SQLite's locking model assumes POSIX byte-range locks work correctly, which is often unreliable on network/object-backed mounts (NFS, EFS, gcsfuse) that `PRIORIS_MCP_STORAGE_DIR` could point at. Append-plus-atomic-rename (the pattern `FilesystemStorageBackend._atomic_write` already uses) degrades far more gracefully there than a shared SQLite file would.

### Full-text search: FTS5 index

One global SQLite + FTS5 index (`search.sqlite3` above) over every persisted `markdown` artefact — not one index per document. It is an **external-content** FTS5 table: it stores tokenized content plus a reference (`provider`, `identifier`, `format`) back to the `markdown` file on disk, rather than duplicating Markdown bytes inside the SQLite file, and it carries `provider`/`identifier`/`format` columns so a query can be scoped to a single document (`MATCH ... AND identifier = ?`) or left unscoped to search everything — the same index serves both "search within this document" and "search everything," just with or without a `WHERE` filter; no separate per-document index files.

A single-document-scoped index was considered and rejected: BM25's IDF term is only meaningful across multiple documents in the first place (a one-document corpus has no document-frequency contrast to compute it from), so a genuinely per-document index couldn't produce more meaningful ranking than the scoped-`WHERE`-over-the-global-index approach — it would only add file-proliferation and per-file lock-contention cost for no ranking benefit. Nor is a persistent index needed at all for "search within one already-fetched document" specifically — an in-memory document's Markdown is small enough that a caller can substring/regex-scan it directly; the FTS5 index exists to avoid the genuinely O(n)-in-corpus-size scan that cross-document search would otherwise require, which is a real problem at this store's target scale (tens of thousands of documents) and is unaffected by a `WHERE`-scoped query — FTS5 resolves `MATCH` through the inverted index's posting lists, not a corpus scan, regardless of how many documents are indexed.

Synced incrementally at the two existing mutation points (`write`/`delete` for the `markdown` artefact) — no triggers, no periodic full rebuild needed, since those are already the only places Markdown content is created or removed. Treated as a pure, disposable cache, never a source of truth: `catalogue.jsonl` and the `markdown` files remain durable; `search.sqlite3` can be deleted and rebuilt from them at any time, and doesn't need to live on the same (possibly cloud-mounted) volume as `catalogue.jsonl` — it can live on local/ephemeral disk where available, sidestepping cloud-storage corruption risk entirely rather than tolerating it. A `kind`/`level` column is reserved on the table now (`leaf` today; `summary`, with a tree depth, reserved for the future document-level content below) so that adding summary-node search later is an additive population of existing rows, not a schema migration.

### Deletion is per-artefact, not per-format

`delete(provider, identifier, format, artefact)` takes an `artefact` of `document`, `markdown`, or `all` — preserving the independent-deletability guarantee the current design already has (today expressed as two separately-deletable *formats*, `pdf` and `pdf-markdown`; here as two separately-deletable *artefacts* within one format directory). A caller can drop the bulky raw `document` while keeping the cheap `markdown`, or vice versa, exactly as before. `artefact="all"` removes the whole format directory (`document`, `markdown`, `structure.jsonl`, `metadata.jsonl`); if that was the last format directory under a `<document-hash>`, the document-hash directory itself is removed too, in v1's scope — see [Future](#future-document-level-generated-content) for how this rule needs to change once document-level generated content exists.

### Identifier canonicalisation

Some providers' identifiers don't have a fixed meaning over time. arXiv is the v1 example: an unversioned identifier (`2601.05525`) means "whatever is currently the latest version," while a versioned identifier (`2601.05525v2`) is permanently pinned, because arXiv versions are immutable once published.

A pinned identifier is a safe, permanent storage key on its own — its content can never change, so it's always safe to reuse a persisted copy. An unversioned identifier is not: the content behind it can legitimately change (a new version gets published) without the identifier string itself changing, so keying storage on the bare unversioned identifier risks silently serving stale content once a newer version exists. Hashing the unversioned string doesn't fix this — it just produces a very stable-looking hash of an answer that isn't stable.

**`resolve_identifier` (see [Architecture](01-architecture.md)) is responsible for resolving an unversioned identifier to its current concrete version before it is used anywhere as a storage key.** Storage itself never sees a bare unversioned identifier — only the canonical, version-pinned one that `resolve_identifier` produced for it, this call, and a pinned identifier passed in by the caller needs no resolution at all. Consequences:

- A canonical (version-pinned) identifier's storage entry can be kept and reused indefinitely.
- An unversioned request may resolve to a different canonical identifier — and therefore land on a different document-hash — once a new version is published, which is correct behaviour, not a bug to work around.
- Resolving "what's current" is a light, metadata-level check, done on every unversioned request regardless of whether anything changed; that per-call cost is the accepted price of never silently serving stale full text, and it's far cheaper than the full-text download it protects against re-serving incorrectly.

Content hashing of the downloaded bytes is a separate concern — useful as an optional integrity check (e.g. detecting a truncated download) — but it does not substitute for version resolution, since it can only detect a change after paying for the download it was meant to avoid.

### Content-hash canonicalisation for the local filesystem source

The [local filesystem source](01-architecture.md#local-filesystem-source) has no equivalent of `resolve_identifier`, because it has no external authority asserting what "the current version" of caller-sent content is — unlike arXiv, where an unversioned identifier's mutability is a known, bounded fact (it always means "whatever arXiv currently says is latest"), caller-sent content's mutability is unbounded and unannounced: the caller can send edited or replaced bytes on any subsequent call, with nothing to notify PriorisMCP.

Content hashing, dismissed above as insufficient *on its own* for network sources (it can only detect staleness after paying for the download), is exactly sufficient here, because reading a local file to hash it is not the expensive operation being protected against — copying it into storage is. Every `fetch_full_text` call for this source reads the file's current bytes and computes their SHA-256 hash unconditionally, then uses `(provider="localfile", identifier=content_hash, format="pdf")` to locate its entry: the document-hash directory is `sha256([provider, content_hash])` — a hash of a hash, distinct from `content_hash` itself, which plays the role of `identifier` here exactly as an arXiv ID or DOI would for other providers. This *is* the canonicalisation step, taking the place `resolve_identifier` fills for arXiv, just performed inline by the local filesystem source's `fetch_full_text` rather than exposed as a separate capability. If the hash already exists in storage, `write` is skipped (a no-op re-fetch); if the file's content has changed since any previous fetch, this produces a new hash and therefore a new, independent storage entry, leaving whatever entry an earlier fetch produced untouched and still validly readable — the same guarantee a pinned arXiv version already provides, arrived at by hashing actual content instead of trusting an external version number.

### Caller-facing identifiers for sources without one

Storage keys are hashed specifically so they're safe to use as a path segment (see [Storage keys are hashed](#storage-keys-are-hashed-not-built-from-the-raw-identifier) above) — but a content hash, while segment-safe, is not something a caller can usefully reuse in conversation, and the local filesystem source has no caller-supplied identifier at all to fall back on: the only caller-supplied value is the base64-encoded content itself (plus an optional, non-identifying `filename` hint that isn't even segment-safe, since it can contain `/`).

The local filesystem source therefore assigns a third, distinct value — a **caller-facing identifier** — at `fetch_full_text` time: a minute-resolution timestamp plus a short random suffix (e.g. `20260729-1430-a3f2`), segment-safe and legible enough for a caller to recognise in a conversation transcript, without needing either the original content or the content hash to refer back to it. This is what `fetch_full_text` returns to the caller, what appears in the resource URI, and what `parse_full_text` subsequently takes as its input — not the content hash (which is an internal storage-key implementation detail, never surfaced).

This gives three distinct values, each with one job, for the local filesystem source specifically:

| Value | Role | Where it's used |
|---|---|---|
| Path | What the caller supplies | Input to `fetch_full_text` only |
| Content hash | The `identifier` fed into the document-hash | Never surfaced to the caller |
| Caller-facing ID | Public identifier | Returned by `fetch_full_text`; input to `parse_full_text`; appears in resource URIs; looked up via the catalogue (see below) |

A catalogue entry (see [The catalogue](#the-catalogue-cataloguejsonl) above) maps each caller-facing ID to its content hash and format, so `parse_full_text` can resolve an ID back to the right storage entry without needing the original content again. Re-fetching unchanged content (same hash) reuses its existing caller-facing ID rather than minting a new one, so a caller who already has an ID for that content keeps using the same one; changed content (new hash) gets a new ID, consistent with [Content-hash canonicalisation](#content-hash-canonicalisation-for-the-local-filesystem-source) above never repointing an existing identifier at different content.

Collision handling for the caller-facing ID needs no shared, persistent counter: on generating an ID, the catalogue is checked for that exact value, and a new random suffix is drawn and rechecked in the rare case of a collision — the minute-resolution timestamp already scopes the collision space to whatever falls within the same minute, and a 4-character base-36 suffix (~1.68M values) keeps that risk low even under a burst of concurrent fetches within one minute.

### Migration

Stores created before this layout landed use the flat `<hash>`/`<hash>.json` scheme (one file pair per `(provider, identifier, format)` triple, `format` included in the hash). A one-time migration walks that flat layout and regroups each entry under `documents/<document-hash>/<format>/`, splitting what was a single `pdf`/`pdf-markdown` pair of format values into `document`/`markdown` artefacts of one `pdf` format directory, and builds `catalogue.jsonl` from the resulting `metadata.jsonl` records. This must be idempotent and safe to run against a store that's a mix of old and new layout (e.g. gated behind a version marker in the storage directory), since there's no way to guarantee every deployment migrates in one atomic step.

## v1: local filesystem backend

The default — and, for v1, only implemented — backend persists to a directory on local disk, under `XDG_DATA_HOME`, not `XDG_CONFIG_HOME`: downloaded content is data, not configuration. If `XDG_DATA_HOME` is unset, the [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html) default of `~/.local/share` applies.

- Default location: `$XDG_DATA_HOME/prioris-mcp/downloads` (i.e. `~/.local/share/prioris-mcp/downloads` when `XDG_DATA_HOME` is unset), laid out as shown in [Directory layout](#directory-layout) above.
- Configurable through an environment variable declared on `EnvVars` (see `src/prioris_mcp/__init__.py`), consistent with how the rest of the server is configured — not hardcoded, and not read from `os.environ` elsewhere.

## Future: S3 (or other remote/object) backend

Explicitly out of scope for v1 (see the out-of-scope list in the [SRS overview](index.md)), but the `exists`/`write`/`read` contract above is designed so an S3-backed implementation is a second implementation of the same interface, selected by configuration — not a different code path through the providers or tools. `catalogue.jsonl` and the FTS5 index are designed with this in mind already: the catalogue is append-friendly (no in-place edits, so no read-modify-write races on an object store), and the FTS5 index is explicitly allowed to live on different storage than the durable data it derives from — a hosted deployment could keep `catalogue.jsonl`/`documents/` on object storage while keeping `search.sqlite3` on the host's local disk.

## Future: document-level generated content

A reserved slot, not a v1 capability: `<document-hash>/summaries.jsonl` will hold a generated, format-independent concept/content hierarchy, produced only by an explicit, non-cascading `generate_full_text_summaries` tool (never triggered automatically by `parse_full_text`, matching the existing precedent that `parse_full_text` never auto-triggers `fetch_full_text`) — out of scope for the storage redesign covered by this document; tracked separately once that tool's own design is settled. Two consequences already accounted for in the design above so this doesn't require a second migration later:

- `summaries.jsonl` lives at the *document* level, shared across all of that document's format directories, specifically so running `generate_full_text_summaries` against one format (e.g. `pdf`) and later against a sibling format of the same document (e.g. `html`) doesn't pay for near-duplicate LLM summarisation twice — the second call is a no-op unless explicitly forced.
- The FTS5 index's reserved `kind`/`level` column (see [Full-text search](#full-text-search-fts5-index) above) is already shaped to accommodate summary-node rows once they exist.

Once implemented, the [deletion rule above](#deletion-is-per-artefact-not-per-format) — removing the last format directory also removes the document-hash directory — will need a guard (e.g. a `purge_summaries` flag, off by default) so that deleting the last format doesn't silently discard an expensive, still-potentially-reusable `summaries.jsonl`; a later fetch of any format for the same document would otherwise land back on the same document-hash and find it still valid.

## Future: retention and redistribution-aware persistence

Two further concerns are explicitly deferred beyond v1 (see [SRS overview → Out of scope for v1](index.md#out-of-scope-for-v1)), and kept distinct from each other despite both bearing on "how long persisted content sticks around":

- **Retention/eviction.** v1's local filesystem backend never evicts anything — persisted full text and Markdown accumulate indefinitely. Because storage keys are derived from immutable canonical identifiers (see [Identifier canonicalisation](#identifier-canonicalisation)), nothing ever becomes *incorrect* by staying persisted — the only concern is unbounded disk growth, not staleness. The intended future direction is a size-based cap with LRU eviction (evicting least-recently-*read* entries once a configured disk quota is reached), using an extended catalogue entry tracking last-read time alongside the existing fetch/parse timestamps. A simpler time-based TTL could be offered as a secondary option, but LRU-by-size is the primary direction since a TTL alone doesn't bound disk usage under heavy recent use, and can evict content that's still being actively reused simply for being old.
- **Redistribution-policy-aware persistence.** Whether persisted content may ever be shared beyond the MCP client that originally fetched it is a licensing question, not a disk-management one, and v1 does not have full visibility into per-article licences to make that decision safely — Europe PMC's metadata already carries a `license` field (see [Interface specification](06-interface-specification.md)), but arXiv's Export API exposes none; only the already-deferred OAI-PMH `arXiv` format does. This is future work gated on that visibility, not on the eviction mechanism above.
