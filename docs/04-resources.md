---
icon: lucide/folder-open
---

# Resources

Alongside its tools, PriorisMCP exposes three read-only MCP resources.

| Resource | Returns |
|---|---|
| `research://{provider}/{identifier}/{format}/fulltext` | The persisted full text for that item/format, if present — backed by [`StorageBackend`](requirement-specification/02-storage.md). |
| `research://{provider}/{identifier}/{format}/markdown{?offset,limit}` | One paginated page of the persisted parsed Markdown for that item/format, if present — also backed by `StorageBackend`. |
| `research://arxiv/categories` | arXiv's queryable category codes and names (e.g. `cs.LG` → "Machine Learning"), sourced live from arXiv's OAI-PMH `ListSets` endpoint and covered by the standard response-cache TTL rather than `StorageBackend`. |

`{provider}` is `arxiv` or `europepmc`; `{identifier}` is the *canonical* identifier (version-pinned for arXiv, `PMC:{pmcid}` for Europe PMC) that the corresponding `fetch_full_text`/`parse_full_text` call resolved to — not necessarily the identifier originally passed to that call; `{format}` is the source format (`pdf`, `html`, `xml`).

The markdown resource's optional `offset`/`limit` query parameters mirror `parse_full_text`'s own pagination (same defaults, same `offset`/`limit`/`total_length`/`has_more` fields in the response) — see [Tools → arXiv tools](03-tools.md#arxiv-tools). A caller can page through previously-parsed content this way without re-invoking the tool.

`research_*_fetch_full_text` and `research_*_parse_full_text` both return the exact `resource_uri` for their result, so a caller doesn't need to construct these URIs by hand.

`research://arxiv/categories` has no corresponding tool: it's read-only reference data an LLM caller can consult to pick a valid category code for `research_arxiv_list_top_n`'s `include_categories`/`exclude_categories` or `research_arxiv_search`'s `cat:` query terms, not an action with inputs to invoke.

## Behaviour

- Reading `fulltext` or `markdown` **never** triggers a fetch or a parse — reading one that doesn't exist yet is a plain not-found, not an error requiring special handling. Call the corresponding tool (see [Tools](03-tools.md)) first.
- `research://arxiv/categories` always attempts a live call to arXiv's OAI-PMH endpoint on a cache miss (there's no persisted-content precondition the way there is for `fulltext`/`markdown`) — repeat reads within `PRIORIS_MCP_RESPONSE_CACHE_TTL` are served from the response cache, not re-fetched.
- There is no per-item metadata resource: metadata is only ever response-cached (see [Tools → Caching and rate limiting](03-tools.md#caching-and-rate-limiting)), never written to `StorageBackend`, so there's no stable location for it the way there is for full text and Markdown.

See [Storage](requirement-specification/02-storage.md) for how `fulltext`/`markdown` content is persisted and keyed, and [Functional requirements → Resources](requirement-specification/03-functional-requirements.md#resources) for the behavioural requirements these implement.
