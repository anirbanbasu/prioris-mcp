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
| `write` | Persist content for a given item/format; returns a location/reference. |
| `read` | Retrieve previously persisted content for a given item/format. |
| `list` | Enumerate persisted manifest entries, optionally filtered by provider/format. |
| `delete` | Remove a persisted item/format (and its manifest entry) if present. |

`fetch_full_text` checks `exists` before performing a network fetch, and can return the already-persisted copy instead of downloading again. This is the mechanism that avoids redundant downloads for full text — it lives in the storage abstraction itself, not in the server's generic response-caching middleware, which is a poor fit for potentially large binary documents.

`list` and `delete` back the grouping-level `research_list_fetched`/`research_delete_fetched` MCP tools (see [Architecture → `list_fetched`/`delete_fetched`](01-architecture.md#list_fetched-delete_fetched-grouping-level)) — a caller-driven way to enumerate or remove specific entries (e.g. correcting a mistakenly-fetched wrong identifier), distinct from the deferred, disk-pressure-driven [retention/eviction](#future-retention-and-redistribution-aware-persistence) policy below. Both operate on whichever manifest entries already exist, for any provider, without touching content that isn't already persisted.

`parse_full_text` follows the same pattern one level up: it checks `exists` for the *parsed* Markdown first and returns it if present; only if that's missing does it check `exists` for the source full text, parse it (CPU-heavy), `write` the Markdown, and return it. It never triggers a `fetch_full_text` itself (see [Architecture](01-architecture.md)) — if the source full text isn't there either, it fails with a "not found" error.

Parsed Markdown is persisted as just another **format** value for the same `(provider, canonical identifier)` key — e.g. a source fetched as format `pdf` produces parsed content stored under a format that identifies it as "Markdown derived from PDF" (distinct from Markdown derived from `html`, since the two conversions can legitimately differ). This reuses the existing `exists`/`write`/`read` contract rather than introducing a second, parallel concept for derived content. Because canonical identifiers are immutable once resolved (see [Identifier canonicalisation](#identifier-canonicalisation)), a persisted parse result is exactly as permanently valid as the source content it was derived from — no separate invalidation logic is needed for it.

### Identity and location

Persisted content is identified by **provider + canonical item identifier + format** (e.g. arXiv item `2601.05525v2`, format `pdf`), not by a filename the caller chooses. This keeps `exists`/`write`/`read` consistent regardless of which backend is in use, and avoids collisions between providers that might otherwise reuse the same identifier scheme. "Canonical" is doing real work here — see [Identifier canonicalisation](#identifier-canonicalisation) below.

#### Storage keys are hashed, not built from the raw identifier

An identifier is not safe to use directly as a filename or path segment: DOIs contain `/` (e.g. `10.1000/xyz123`), identifiers can contain arbitrary characters, and filenames built from external input are a path-traversal risk if anything ever produces a malformed or adversarial-looking identifier. Enumerating and escaping every unsafe character is easy to get subtly wrong; filename-length limits are a further, separate constraint.

Storage keys are therefore derived by **hashing** `(provider, canonical identifier, format)` — e.g. a SHA-256 hex digest — rather than encoding the identifier into the path. The output is always a fixed-length, filesystem-safe string, which removes the character-safety and path-traversal concerns entirely rather than mitigating them case by case.

Human-readability is preserved separately: each persisted item has a small sidecar/manifest entry (provider, original identifier, canonical identifier, format, fetch timestamp, ...) rather than a descriptive filename. This also gives future eviction/inspection tooling a natural place to look.

### Identifier canonicalisation

Some providers' identifiers don't have a fixed meaning over time. arXiv is the v1 example: an unversioned identifier (`2601.05525`) means "whatever is currently the latest version," while a versioned identifier (`2601.05525v2`) is permanently pinned, because arXiv versions are immutable once published.

A pinned identifier is a safe, permanent storage key on its own — its content can never change, so it's always safe to reuse a persisted copy. An unversioned identifier is not: the content behind it can legitimately change (a new version gets published) without the identifier string itself changing, so keying storage on the bare unversioned identifier risks silently serving stale content once a newer version exists. Hashing the unversioned string doesn't fix this — it just produces a very stable-looking hash of an answer that isn't stable.

**`resolve_identifier` (see [Architecture](01-architecture.md)) is responsible for resolving an unversioned identifier to its current concrete version before it is used anywhere as a storage key.** Storage itself never sees a bare unversioned identifier — only the canonical, version-pinned one that `resolve_identifier` produced for it, this call, and a pinned identifier passed in by the caller needs no resolution at all. Consequences:

- A canonical (version-pinned) identifier's storage entry can be kept and reused indefinitely.
- An unversioned request may resolve to a different canonical identifier — and therefore land on a different storage key — once a new version is published, which is correct behaviour, not a bug to work around.
- Resolving "what's current" is a light, metadata-level check, done on every unversioned request regardless of whether anything changed; that per-call cost is the accepted price of never silently serving stale full text, and it's far cheaper than the full-text download it protects against re-serving incorrectly.

Content hashing of the downloaded bytes is a separate concern — useful as an optional integrity check (e.g. detecting a truncated download) — but it does not substitute for version resolution, since it can only detect a change after paying for the download it was meant to avoid.

### Content-hash canonicalisation for the local filesystem source

The [local filesystem source](01-architecture.md#local-filesystem-source) has no equivalent of `resolve_identifier`, because it has no external authority asserting what "the current version" of caller-sent content is — unlike arXiv, where an unversioned identifier's mutability is a known, bounded fact (it always means "whatever arXiv currently says is latest"), caller-sent content's mutability is unbounded and unannounced: the caller can send edited or replaced bytes on any subsequent call, with nothing to notify PriorisMCP.

Content hashing, dismissed above as insufficient *on its own* for network sources (it can only detect staleness after paying for the download), is exactly sufficient here, because reading a local file to hash it is not the expensive operation being protected against — copying it into storage is. Every `fetch_full_text` call for this source reads the file's current bytes and computes their SHA-256 hash unconditionally, then uses `(provider="localfile", content_hash, format="pdf")` as the storage key: this *is* the canonicalisation step, taking the place `resolve_identifier` fills for arXiv, just performed inline by the local filesystem source's `fetch_full_text` rather than exposed as a separate capability. If the hash already exists in storage, `write` is skipped (a no-op re-fetch); if the file's content has changed since any previous fetch, this produces a new hash and therefore a new, independent storage entry, leaving whatever entry an earlier fetch produced untouched and still validly readable — the same guarantee a pinned arXiv version already provides, arrived at by hashing actual content instead of trusting an external version number.

### Caller-facing identifiers for sources without one

Storage keys are hashed specifically so they're safe to use as a path segment (see [Storage keys are hashed](#storage-keys-are-hashed-not-built-from-the-raw-identifier) above) — but a content hash, while segment-safe, is not something a caller can usefully reuse in conversation, and the local filesystem source has no caller-supplied identifier at all to fall back on: the only caller-supplied value is the base64-encoded content itself (plus an optional, non-identifying `filename` hint that isn't even segment-safe, since it can contain `/`).

The local filesystem source therefore assigns a third, distinct value — a **caller-facing identifier** — at `fetch_full_text` time: a minute-resolution timestamp plus a short random suffix (e.g. `20260729-1430-a3f2`), segment-safe and legible enough for a caller to recognise in a conversation transcript, without needing either the original content or the content hash to refer back to it. This is what `fetch_full_text` returns to the caller, what appears in the resource URI, and what `parse_full_text` subsequently takes as its input — not the content hash (which is an internal storage-key implementation detail, never surfaced).

This gives three distinct values, each with one job, for the local filesystem source specifically:

| Value | Role | Where it's used |
|---|---|---|
| Path | What the caller supplies | Input to `fetch_full_text` only |
| Content hash | Internal storage key | Never surfaced to the caller |
| Caller-facing ID | Public identifier | Returned by `fetch_full_text`; input to `parse_full_text`; appears in resource URIs; looked up via the manifest (see below) |

A manifest entry (see [Storage keys are hashed](#storage-keys-are-hashed-not-built-from-the-raw-identifier) above) maps each caller-facing ID to its content hash and format, so `parse_full_text` can resolve an ID back to the right storage entry without needing the original content again. Re-fetching unchanged content (same hash) reuses its existing caller-facing ID rather than minting a new one, so a caller who already has an ID for that content keeps using the same one; changed content (new hash) gets a new ID, consistent with [Content-hash canonicalisation](#content-hash-canonicalisation-for-the-local-filesystem-source) above never repointing an existing identifier at different content.

Collision handling for the caller-facing ID needs no shared, persistent counter: on generating an ID, the manifest is checked for that exact value, and a new random suffix is drawn and rechecked in the rare case of a collision — the minute-resolution timestamp already scopes the collision space to whatever falls within the same minute, and a 4-character base-36 suffix (~1.68M values) keeps that risk low even under a burst of concurrent fetches within one minute.

## v1: local filesystem backend

The default — and, for v1, only implemented — backend persists to a directory on local disk, under `XDG_DATA_HOME`, not `XDG_CONFIG_HOME`: downloaded content is data, not configuration. If `XDG_DATA_HOME` is unset, the [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html) default of `~/.local/share` applies.

- Default location: `$XDG_DATA_HOME/prioris-mcp/downloads` (i.e. `~/.local/share/prioris-mcp/downloads` when `XDG_DATA_HOME` is unset).
- Configurable through an environment variable declared on `EnvVars` (see `src/prioris_mcp/__init__.py`), consistent with how the rest of the server is configured — not hardcoded, and not read from `os.environ` elsewhere.

## Future: S3 (or other remote/object) backend

Explicitly out of scope for v1 (see the out-of-scope list in the [SRS overview](index.md)), but the `exists`/`write`/`read` contract above is designed so an S3-backed implementation is a second implementation of the same interface, selected by configuration — not a different code path through the providers or tools.

## Future: retention and redistribution-aware persistence

Two further concerns are explicitly deferred beyond v1 (see [SRS overview → Out of scope for v1](index.md#out-of-scope-for-v1)), and kept distinct from each other despite both bearing on "how long persisted content sticks around":

- **Retention/eviction.** v1's local filesystem backend never evicts anything — persisted full text and Markdown accumulate indefinitely. Because storage keys are content-addressed from immutable canonical identifiers (see [Identifier canonicalisation](#identifier-canonicalisation)), nothing ever becomes *incorrect* by staying persisted — the only concern is unbounded disk growth, not staleness. The intended future direction is a size-based cap with LRU eviction (evicting least-recently-*read* entries once a configured disk quota is reached), using an extended sidecar/manifest entry (see [Storage keys are hashed](#storage-keys-are-hashed-not-built-from-the-raw-identifier)) tracking last-read time alongside the existing fetch timestamp. A simpler time-based TTL could be offered as a secondary option, but LRU-by-size is the primary direction since a TTL alone doesn't bound disk usage under heavy recent use, and can evict content that's still being actively reused simply for being old.
- **Redistribution-policy-aware persistence.** Whether persisted content may ever be shared beyond the MCP client that originally fetched it is a licensing question, not a disk-management one, and v1 does not have full visibility into per-article licences to make that decision safely — Europe PMC's metadata already carries a `license` field (see [Interface specification](06-interface-specification.md)), but arXiv's Export API exposes none; only the already-deferred OAI-PMH `arXiv` format does. This is future work gated on that visibility, not on the eviction mechanism above.
