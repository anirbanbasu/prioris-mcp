---
icon: lucide/folder-open
---

# Resources

Alongside its tools, PriorisMCP exposes six read-only MCP resources.

| Resource | Returns |
|---|---|
| `research://{provider}/{identifier}/{format}/fulltext` | The persisted full text for that item/format, if present — backed by [`StorageBackend`](requirement-specification/storage/01-document-storage.md). |
| `research://{provider}/{identifier}/{format}/markdown{?offset,limit,page}` | One paginated page of the persisted parsed Markdown for that item/format, if present — also backed by `StorageBackend`. |
| `research://arxiv/categories` | arXiv's queryable category codes and names (e.g. `cs.LG` → "Machine Learning"), sourced live from arXiv's OAI-PMH `ListSets` endpoint and covered by the standard response-cache TTL rather than `StorageBackend`. |
| `research://openalex/work-types` | OpenAlex's work `type` vocabulary (code, display name, one-line description; 25 entries as of writing), sourced live from OpenAlex's `/work-types` endpoint and covered by the standard response-cache TTL, same as `research://arxiv/categories` — reference data for interpreting a [`research_discovery`](03-tools.md#discovery-tool) hit's own metadata, not a filter parameter that tool accepts. |
| `notes://{note_id}/export` | That note's file representation — a `NoteExport` (JSON, not YAML frontmatter) with `suggested_filename`, `frontmatter` (every `Note` field except `text`), and `markdown_body` (exactly the note's own `text`) — backed by [`NotesBackend`](requirement-specification/storage/02-notes-storage.md). |
| `research://vector-index/rebuild-status` | Corpus-wide vector-index reconciliation's live progress — a `VectorRebuildStatus` with `documents`/`notes`, each `{"total", "remaining"}` counting only items the most recent reconciliation run decided needed rebuilding — see [Tools → Vector index reconciliation](03-tools.md#vector-index-reconciliation) and [ADR-00030](requirement-specification/ADR/00030-vector-index-reconciliation.md). Process-local and in-memory (`VectorRebuildProgress`), not backed by any persisted storage — resets to a fresh count on every server restart. |

`{provider}` is `arxiv` or `europepmc`; `{identifier}` is the *canonical* identifier (version-pinned for arXiv, `PMC:{pmcid}` for Europe PMC) that the corresponding `fetch_full_text`/`parse_full_text` call resolved to — not necessarily the identifier originally passed to that call; `{format}` is the source format (`pdf`, `html`, `xml`).

For `provider=localfile`, `{identifier}` is the server-assigned caller-facing identifier `research_localfile_fetch_full_text` returned — there is no server-side path to use instead, since the caller sends content directly.

The markdown resource's optional `offset`/`limit` query parameters mirror `parse_full_text`'s own pagination (same defaults, same `offset`/`limit`/`total_length`/`has_more` fields in the response) — see [Tools → arXiv tools](03-tools.md#arxiv-tools). A caller can page through previously-parsed content this way without re-invoking the tool. `page` (1-indexed, `pdf` format only) mirrors `parse_full_text`'s own `page` parameter: it's resolved directly against that document's persisted manifest, so `offset` becomes relative to the page's start rather than to the whole document; requesting `page` before `parse_full_text` has ever populated the manifest is a not-found, not a silent fallback to character offset 0. The response's `total_pages`/`page_range` fields are populated the same way as `parse_full_text`'s.

`research_*_fetch_full_text` and `research_*_parse_full_text` both return the exact `resource_uri` for their result, so a caller doesn't need to construct these URIs by hand.

`research://arxiv/categories` has no corresponding tool: it's read-only reference data an LLM caller can consult to pick a valid category code for `research_arxiv_list_top_n`'s `include_categories`/`exclude_categories` or `research_arxiv_search`'s `cat:` query terms, not an action with inputs to invoke. `research://openalex/work-types` is the same shape of thing, one level removed: it's reference data for interpreting a `research_discovery` hit's own metadata after the fact, not something `research_discovery` itself takes as an input.

## Behaviour

- Reading `fulltext` or `markdown` **never** triggers a fetch or a parse — reading one that doesn't exist yet is a plain not-found, not an error requiring special handling. Call the corresponding tool (see [Tools](03-tools.md)) first.
- `research://arxiv/categories` and `research://openalex/work-types` both always attempt a live call to their respective upstream endpoint on a cache miss (there's no persisted-content precondition the way there is for `fulltext`/`markdown`) — repeat reads within `PRIORIS_MCP_RESPONSE_CACHE_TTL` are served from the response cache, not re-fetched.
- There is no per-item metadata resource: metadata is only ever response-cached (see [Tools → Caching and rate limiting](03-tools.md#caching-and-rate-limiting)), never written to `StorageBackend`, so there's no stable location for it the way there is for full text and Markdown.
- `notes://{note_id}/export` never writes anything to disk itself — it hands the caller a `NoteExport`, and the caller is responsible for writing `frontmatter`/`markdown_body` to a file if it wants one; see [Security → Notes export does not write files](requirement-specification/05-security.md#notes-export-does-not-write-files). Unlike the other three resources above, it is **never** served from the response cache, because notes are mutable (create/update/delete) — see [Tools → Caching and rate limiting](03-tools.md#caching-and-rate-limiting) for why, and `NotesCacheBypassMiddleware` for the mechanism.

See [Storage](requirement-specification/storage/01-document-storage.md) for how `fulltext`/`markdown` content is persisted and keyed, and [Functional requirements → Resources](requirement-specification/03-functional-requirements.md#resources) for the behavioural requirements these implement.
