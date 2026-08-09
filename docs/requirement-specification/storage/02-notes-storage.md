---
icon: lucide/sticky-note
---

# Notes storage

**v2** — see [SRS overview → Scope](../index.md#v2) and [Architecture → `NotesBackend`](../01-architecture.md#notesbackend) for why this is a new abstraction, not an extension of [`StorageBackend`](01-document-storage.md#storagebackend).

## Purpose

The `prioris` client plugin currently keeps user-authored paper notes (discussion synthesis, open questions, quiz recaps) in local Markdown files under `.prioris/discussions/`, entirely outside `prioris-mcp` — see issue #13. `NotesBackend` is the server-side store those notes move to: identity-addressed and mutable, the opposite of `StorageBackend`'s content-addressed, write-once fetched content.

**v2 scope is single-user only**, mirroring how `StorageBackend` itself started local-only in v1: no `user_id` column, one notes store per PriorisMCP instance. Multi-user/hosted deployment — the same local/hosted split `StorageBackend` already documents for itself (see [Storage → Embedded SQLite vs. a client-server database](01-document-storage.md#embedded-sqlite-vs-a-client-server-database)) — is explicit future work, not designed here.

## `NotesBackend`

### Interface

| Operation | Description |
|---|---|
| `create` | Persist a new note, keyed by `(provider, canonical_identifier, format)`; generates its id and timestamps. |
| `read` | Retrieve a single note by id. |
| `update` | Partially edit an existing note's `text`/`anchors`/`tags`/`metadata` in place; `provider`/`canonical_identifier`/`format`/`author_name` are immutable after creation — changing "which document/who" is a new note, not an edit. |
| `delete` | Remove a note by id. |
| `search` | Search/list notes by any combination of provider/identifier/format/date range/keyword/author/tags, paginated; no filters at all is "list everything" — see [Architecture → Listing is `search` with no filters](../01-architecture.md#listing-is-search-with-no-filters-not-a-separate-tool). |
| `export` | Return one note's file representation (`suggested_filename`, `frontmatter`, `markdown_body`) for the caller to write to disk itself — exposed as a resource, not a tool, see [Architecture → Export is a resource, not a tool](../01-architecture.md#export-is-a-resource-not-a-tool). |

`canonical_identifier` must already be resolved/pinned by the time it reaches `NotesBackend`, mirroring [Storage → Identifier canonicalisation](01-document-storage.md#identifier-canonicalisation) — resolution is the calling tool's job, not `NotesBackend`'s, the same split `StorageBackend` already has with `resolve_identifier`. When `format` is omitted (a note predating any fetch), resolution is skipped entirely and the given identifier is stored as-is: there is no persisted artefact whose storage-key stability this needs to protect yet.

## Data model

A given `(provider, canonical_identifier, format)` can have any number of notes over time — each its own row/id — not one evolving blob per document.

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (UUID) | Primary key; also the export filename stem. |
| `provider` | TEXT | e.g. `arxiv`, `europepmc`, `localfile`. |
| `canonical_identifier` | TEXT | Pinned/resolved identifier — never a bare unversioned one. |
| `format` | TEXT, nullable | Nullable because a note can predate any fetch (e.g. "want to read this"). |
| `text` | TEXT | Free-form Markdown — the note itself. The only column indexed by keyword search. |
| `anchors` | TEXT (JSON array of `Anchor`), default `[]` | Unresolved, unenforced positional hints — see below. Excluded from keyword search. |
| `author_name` | TEXT, nullable | `NULL` means "self." A plain display label, not an identity/auth concept — doesn't imply multi-user support (see [Purpose](#purpose) above); exists so e.g. a student and their supervisor can distinguish whose note is whose on a shared local store. |
| `tags` | TEXT (JSON array of `str`), default `[]` | Exact-match filterable (see [`search`](#search-is-structured-filtering-plus-optional-keyword-matching) below), not keyword-indexed. |
| `metadata` | TEXT (JSON object, `dict[str, str]`), nullable | Caller-owned, opaque scratch space — see below. |
| `created_at` / `updated_at` | TIMESTAMP | ISO 8601. |

### `anchors`: unresolved positional hints, not pointers

Borrows shape from the [W3C Web Annotation Data Model](https://www.w3.org/TR/annotation-model/)'s selector concept (`TextQuoteSelector`'s `exact`/`prefix`/`suffix`, adapted here as `exact_text_quote`/`prefix_context`/`suffix_context`) without adopting it as a formal dependency — `page_number`/`section_heading`/`paragraph_index` are this project's own addition, not a standard W3C selector type.

```python
class AnchorLocation(BaseModel):
    page_number: int | None = None
    section_heading: str | None = None
    paragraph_index: int | None = None

class AnchorSelectors(BaseModel):
    exact_text_quote: str | None = None
    prefix_context: str | None = None
    suffix_context: str | None = None

class Anchor(BaseModel):
    location: AnchorLocation | None = None
    selectors: AnchorSelectors | None = None
```

None of this is validated or resolved against `manifest.sqlite` — no coupling to spans, no re-parse drift to handle, consistent with [Architecture → Anchoring](../01-architecture.md#anchoring-document-level-not-span-level). It exists purely so a human (or an agent acting on explicit user input) can leave a precise-enough breadcrumb to relocate a passage by eye later. The one validation enforced at write time: an `Anchor` entry with both `location` and `selectors` absent (or all-null within them) is rejected — a fully-empty entry carries no information.

`anchors` is intentionally excluded from keyword search (see [Storage layout](#storage-layout) below) — mixing positional text like "page 4" into a keyword/semantic search index would pollute relevance ranking with information that has nothing to do with a note's actual content.

### `author_name`, `tags`, `metadata`

- **`author_name`** default `NULL` means "self," to avoid writing a literal `"self"`/`"me"` value on the overwhelming majority of notes. This makes "notes I wrote" and "no author filter at all" two different queries (`author_name IS NULL` vs. no predicate at all), not the same one — a plain nullable field can't express that distinction unambiguously on its own; see [`search` is structured filtering plus optional keyword matching](#search-is-structured-filtering-plus-optional-keyword-matching) below for the tri-state that actually expresses it.
- **`tags`** is a plain, exact-match label system (JSON array, same "list even though the common case is small" precedent `manifest.sqlite`'s own `spans` column already sets), not keyword-indexed. Filtering combines three explicit list parameters (must-have-all / must-have-any / must-have-none) rather than a general boolean query grammar; the latter is a materially bigger feature (a parser or structured nested-filter object, its own combinatorial test coverage) not justified at this store's expected scale.
- **`metadata`** is caller-owned, opaque scratch space, modelled on Stripe's/GitHub's `metadata` fields: `NotesBackend` persists and returns it verbatim, never interprets, validates, or searches it. Values are plain strings; a caller wanting structured data JSON-encodes it into the string value themselves. Deliberately **not** filterable via `search` — if some key turns out to matter enough to filter/search by, that's the signal it should graduate to a real, typed, indexed column (the way `tags`/`author_name` already are), not stay in the opaque bag.

### `search` is structured filtering plus optional keyword matching

Every filter except keyword (`provider`, `canonical_identifier`, `format`, a date range, an author filter, and the three tag-membership lists) is a plain structured predicate against `notes.sqlite`. The author filter is a tri-state, not a nullable string — `ANY` (no author filtering), `MINE` (`author_name IS NULL`), or `NAMED` (`author_name = <value>`) — for the same reason `author_name`'s own `NULL`-means-self default needs one: a single nullable parameter can't distinguish "no filter" from "filter to a null author." Results are paginated (`offset`/`limit`, total count, `has_more`), mirroring the shape [Non-functional requirements → Response size](../04-non-functional-requirements.md#response-size) already establishes for `parse_full_text`, and default-ordered `created_at DESC` (most recent first) when no keyword is given; a keyword search orders by relevance instead.

`canonical_identifier` given without `provider` is rejected (`invalid_request`) rather than silently searched across every provider. `search` performs no resolution of its own — by the time a value reaches it, `canonical_identifier` is just a plain equality predicate against a stored column (see [`NotesBackend`](#notesbackend) above), not something `search` can disambiguate. Canonical identifiers are only unique *within* a provider's own scheme (arXiv's `2601.05525v2` shape, Europe PMC's, the local filesystem source's server-minted `YYYYMMDD-HHmm-XXXX` — see [Interface specification → `research_notes_create`](../06-interface-specification.md#research_notes_create)), not guaranteed unique globally across providers; requiring `provider` alongside it closes that (currently theoretical, given how distinct those three shapes are) cross-provider collision rather than relying on it never happening.

## Storage layout

New sibling root, `$XDG_DATA_HOME/prioris-mcp/notes/`, next to `downloads/` (not inside it — notes are user-authored, not fetched content, and `downloads/` is purpose-built for the latter, per [Storage → Purpose](01-document-storage.md#purpose)). Configured by a new `EnvVars` entry (`PRIORIS_MCP_NOTES_DIR`), the same env-var-driven pattern `PRIORIS_MCP_STORAGE_DIR` already uses.

```
$XDG_DATA_HOME/prioris-mcp/notes/
  notes.sqlite            durable, source of truth — the `notes` table above
  notes-search.sqlite3    disposable FTS5 cache, rebuildable from notes.sqlite
```

### `notes-search.sqlite3` is deliberately minimal — a departure from `search.sqlite3`'s precedent

`search.sqlite3` denormalizes `provider`/`identifier`/`format` as `UNINDEXED` columns alongside its tokenized text specifically to avoid needing "separate per-document index files" (see [Storage → Full-text search](01-document-storage.md#full-text-search-the-searchsqlite3-index)). Notes never had that problem to begin with — there is already exactly one global `notes.sqlite`, not one per document — so that reason for denormalizing doesn't apply here. `notes-search.sqlite3`'s FTS5 table holds just a note `id` (`UNINDEXED`) and tokenized `text`.

`search` is implemented as: apply every structured predicate directly against `notes.sqlite`; only if a keyword is given, additionally intersect with the note `id`s matching `notes-search.sqlite3`. This avoids duplicating columns across two files — one less place for the two to drift apart — a reasonable trade against `search.sqlite3`'s single-query optimisation given the much smaller expected scale of a personal notes corpus versus "tens of thousands of documents."

Sync: `create`/`update`/`delete` each touch the FTS5 row directly and immediately (upsert or remove exactly one row) — simpler than `search.sqlite3`'s sync model, since notes are already row-granular and don't come from a bulk parse step.

Concurrency and the embedded-vs.-hosted story are identical to `StorageBackend`'s existing treatment — see [Storage → Embedded SQLite vs. a client-server database](01-document-storage.md#embedded-sqlite-vs-a-client-server-database) — and aren't re-derived here.

## Export

`export` returns `{suggested_filename, frontmatter, markdown_body}` for exactly one note — **one file per note**, not one file per document: it matches the underlying data model 1:1 (each note is already its own row/id) and is the more common Obsidian convention — atomic notes that link to each other and to a paper via frontmatter, rather than long files with internal anchors requiring an in-file structure to represent them.

`frontmatter` carries every non-`text` column verbatim — `id`, `provider`, `canonical_identifier`, `format`, `anchors`, `author_name`, `tags`, `metadata`, `created_at`, `updated_at` — as a plain JSON object (see [Interface specification → Notes](../06-interface-specification.md#notes)), not pre-rendered YAML text: the caller renders it into whatever frontmatter dialect its target tool expects (YAML for an Obsidian vault or any other Markdown-plus-frontmatter tool, `tags` as native tags and the rest as queryable properties) when it writes the file. `markdown_body` is exactly the note's own `text` column, unmodified. See [Architecture → Export is a resource, not a tool](../01-architecture.md#export-is-a-resource-not-a-tool) for why the server itself never writes this to disk.

## Future: cross-document notes and relations

Not designed here, and explicitly out of scope for v2 (see [SRS overview → Out of scope for v2](../index.md#v2)): every v2 note has exactly one **primary** document (`provider`/`canonical_identifier`/`format`, immutable — see [`NotesBackend`](#notesbackend) above). A note observing something about *other* documents beyond that primary one — e.g. while reading and annotating one paper, also noting that several other papers handle the same problem differently — is only expressible today as free text inside `text` itself, with no structured, queryable granularity to it.

This is distinct from `GraphBackend` (see [SRS overview → Out of scope for v2](../index.md#v2)): `GraphBackend` is about PriorisMCP *discovering* relationships between documents on its own (citation structure, semantic overlap); this is about capturing a relationship a human has already declared while writing a note — no discovery/inference involved, and a much smaller capability than a derived-index abstraction.

The intended future direction is additive to the v2 design above, not a redesign of it: a new table (e.g. `note_document_relations`, keyed by `note_id` plus a secondary document's own `provider`/`canonical_identifier`/`format`) sitting alongside a note's existing primary-document identity, not replacing it. Each row would carry a structured `DocumentNotesRelation` rather than a plain relation string, to capture richer relation information (e.g. a relation type/label alongside free-text description) than a single opaque string could — its exact shape isn't designed yet. Because a note's primary document stays exactly as specified above, none of `create`/`read`/`update`/`delete`/`search`/`export`'s current contracts need to change to add this later; it would land as new, optional surface area (a new tool or two, an additive `export` frontmatter key) rather than a breaking change to what v2 already ships.
