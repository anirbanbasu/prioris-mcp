---
icon: lucide/gauge
---

# Non-functional requirements

This page covers cross-cutting qualities that apply across capabilities and providers, rather than the behaviour of any one tool — the counterpart to [Functional requirements](03-functional-requirements.md). v1 scopes this to **concurrency**, since it's the one cross-cutting property the SRS so far has been silent on despite it directly constraining the design in [Architecture](01-architecture.md) and [Storage](02-storage.md). Other NFR categories (performance targets, availability, observability, ...) are future work, not addressed yet.

## Concurrency

An MCP client (an LLM) may legitimately invoke variations of the same tool in parallel — for example, several `research_arxiv_search` calls with different keywords, or several `research_arxiv_fetch_full_text` calls for different items, issued together rather than one at a time. (Batch `fetch_metadata` — see [Functional requirements](03-functional-requirements.md) — exists precisely so that fetching metadata for several identifiers is one call, not several concurrent ones; it is deliberately not the driving example here.) v1 must support this without corrupting state or silently violating a source's terms of use.

### Rate limiting must serialise, not just gate

[Architecture → Caching and rate limiting](01-architecture.md#caching-and-rate-limiting) states each provider's outbound rate limit (arXiv: documented 1 request per 3 seconds; Europe PMC: the same, self-imposed — see [Functional requirements](03-functional-requirements.md)). A check that only asks "has 3 seconds passed since the last request?" is correct for sequential calls but insufficient under concurrency: several parallel tool invocations against the same provider can each see "yes, enough time has passed" and fire together, breaching the limit.

Each provider must therefore serialise its own outbound requests through a single queue (or equivalent), so that concurrent tool invocations against that provider are admitted to the network one at a time, spaced according to its rate limit, rather than racing against a shared check. This applies within a provider; it does not require coordinating across providers, since the rate limit itself is per-source.

### Rate-limit breaches are handled inside the provider's queue, not by the caller

The serialised queue above is designed so that a rate-limit breach (an HTTP 429 from arXiv or Europe PMC) should not happen at all under normal operation with a single PriorisMCP instance — it's prevented by construction, not detected after the fact. A 429 arriving despite the queue therefore indicates something outside this SRS's control: clock drift in the spacing, a second PriorisMCP instance sharing the same outbound IP without coordinating, or the source tightening its limits unilaterally.

When a 429 does occur, the provider's queue must handle it with an adaptive backoff, not surface it to the MCP caller as an immediate failure: on a 429, the queue doubles its wait before retrying that single request — **3s, 6s, 12s, 24s, 48s, ...**, starting from the documented base spacing — transparent to the tool call that triggered it; spacing decays back toward that 3-second base after a sustained run of successful requests. This keeps rate limiting a provider concern end-to-end, consistent with [Architecture → Caching and rate limiting](01-architecture.md#caching-and-rate-limiting), rather than leaking upstream HTTP failure handling into the MCP tool contract.

The total backoff time within a single tool call is bounded, not indefinite, and deliberately short: MCP tool calls are synchronous, and most MCP clients enforce their own tool-call timeout well under a minute, so a long internal retry buys nothing once the client has already given up on the call. The default total backoff budget is **60 seconds** — once the doubling sequence would exceed the remaining budget, the provider gives up and returns a distinct `rate_limited` error rather than retrying further or hanging the call, the same bounded, typed-failure principle [Security](05-security.md#fetched-content-is-untrusted-input-to-parse_full_text) already applies to `parse_full_text` on pathological input. This budget should be a configurable `EnvVars` entry (per `src/prioris_mcp/__init__.py`'s existing pattern), not a hardcoded constant, since the right value depends on the actual MCP client's own timeout, which this SRS cannot assume.

### `provider_unavailable` failures are not retried

A non-429 upstream failure — a timeout, a connection error, or a 5xx response from arXiv or Europe PMC — is a different kind of failure from a 429 and is handled differently: it surfaces immediately as `provider_unavailable` (see [Interface specification](06-interface-specification.md)), with no retry inside the queue. Unlike a 429, which has a clear, well-understood recovery signal (wait, then the fixed-rate window reopens), a timeout or 5xx carries no such guarantee — retrying it blindly risks compounding delay on top of the rate-limit queue's own backoff for no known benefit, so the queue reports it immediately and lets the caller decide whether and when to retry.

Because a `provider_unavailable` isn't retried, a too-tight client-side timeout directly produces spurious failures indistinguishable from a real outage — `httpx.AsyncClient`'s own default (5 seconds on connect/read/write/pool) is tighter than `export.arxiv.org` or Europe PMC can be relied on to respond within under ordinary load. The outbound HTTP client's timeout is therefore a configurable `EnvVars` entry, `PRIORIS_MCP_HTTP_TIMEOUT_SECONDS` (default 30 seconds), not hardcoded or left on httpx's default, for the same reason the rate-limit backoff budget above is configurable: the right value depends on conditions this SRS cannot assume.

The `ProviderUnavailableError` message raised for a transport-level failure must always name the underlying exception type (e.g. `ReadTimeout`, `ConnectError`), not just interpolate `str(exception)` — several `httpx.TransportError` subclasses stringify to an empty string, which would otherwise produce a `provider_unavailable` message with no diagnostic content at all.

For the same reason, every arXiv call goes to `https://export.arxiv.org/api/query` directly rather than `http://` — `export.arxiv.org` 301-redirects every plain-`http` request to `https`, so calling `http` would spend part of the timeout budget above on an extra round-trip before the real request even starts, for no benefit.

### Storage must de-duplicate in-flight work, not just completed work

[Storage → `StorageBackend`](02-storage.md#storagebackend) already de-duplicates *completed* downloads and parses via `exists`. That's insufficient under concurrency: if two calls for the same `(provider, canonical identifier, format)` key arrive close together, both can observe `exists` as false before either has finished `write`-ing, and both proceed — a duplicate concurrent download (for `fetch_full_text`) or a duplicate concurrent parse (for `parse_full_text`) for the same key.

`StorageBackend` (or the layer calling it) must guarantee that only one fetch and only one parse is ever in flight for a given key at a time: a second concurrent request for a key already being fetched or parsed must wait for that in-flight operation to complete and be served its result, rather than starting a redundant one. This is an in-flight lock, distinct from `exists` (which only reflects already-completed work).

### Cross-process storage concurrency is bounded by embedded SQLite's topology

The in-flight lock above is process-local (an `asyncio.Lock`): it prevents two concurrent tool calls *within one PriorisMCP process* from racing on the same key, but does nothing for two independent PriorisMCP *processes* sharing the same storage root (e.g. two parallel agent sessions, each launching its own MCP server against the same `PRIORIS_MCP_STORAGE_DIR`). That guarantee instead comes from `catalogue.sqlite`/`manifest.sqlite`/`search.sqlite3`'s own SQLite-level atomicity (unique constraints, `INSERT ... ON CONFLICT`), which holds across any number of local writer processes sharing one properly lock-capable filesystem — see [Storage → Embedded SQLite vs. a client-server database](02-storage.md#embedded-sqlite-vs-a-client-server-database).

This holds only for that topology: any number of writer processes on one machine, sharing one local, non-network-mounted, non-cloud-synced filesystem. A deployment where writer processes can't share a single such filesystem — genuinely different machines or replicas — must use a real client-server database in front of the catalogue/manifest/search roles instead, per the same section of [Storage](02-storage.md#embedded-sqlite-vs-a-client-server-database); that topology is out of scope for v1, which targets single-machine deployment only.

## Response size

### Inline text is paginated, not returned whole

`parse_full_text` converts an already-fetched source (a PDF, HTML, or JATS XML document) to Markdown. That Markdown is not bounded in size the way most other tool responses are — a large paper can produce tens of thousands of characters — and an MCP client enforces its own ceiling on how large a single tool result may be, independent of anything this server controls. Returning the whole string inline risks exceeding that ceiling, which surfaces to the caller as an opaque client-side failure rather than a typed error from this server.

`parse_full_text` therefore accepts `offset` and `limit` (character-based, zero-indexed) and returns one bounded page of the Markdown, not the whole string: `{"markdown": ..., "offset", "limit", "total_length", "has_more", "resource_uri"}`. `limit` defaults to a configurable `EnvVars` entry, `PRIORIS_MCP_MAX_INLINE_CHARS` (default 20000 characters), for the same reason the timeout and backoff-budget entries above are configurable — the right value depends on the calling client's own ceiling, which this SRS cannot assume. A caller that needs the rest pages through with a later call using the returned `total_length`/`has_more` to know when to stop.

The `research://{provider}/{identifier}/{format}/markdown` resource template accepts the same `offset`/`limit` query parameters (`research://.../markdown{?offset,limit}`) and applies identical pagination, so a caller can page through previously-parsed content via a resource read instead of re-invoking the tool. `research://.../fulltext` (the raw, unparsed source) is not paginated — it is never returned inline in a tool response in the first place (`fetch_full_text` returns only `location`/`size_bytes`/`resource_uri`), so it is not subject to the same failure mode.

## Dependency selection

Where a capability has multiple viable library choices — converting fetched full text to Markdown (see [Architecture → `parse_full_text`](01-architecture.md#parse_full_text)) is the concrete v1 example — prefer widely-used, actively-maintained libraries with fast native (C, Rust, or similar) backends over pure-Python alternatives, since parsing is CPU-heavy and a slow implementation is felt directly in tool-call latency.

License compatibility is checked before performance or popularity, not after: a copyleft dependency (GPL, AGPL) is avoided regardless of how fast or well-regarded it is. This matters more than the usual "check the licence" caution for a project shaped like PriorisMCP specifically: it is MIT-licensed, and for AGPL in particular, the copyleft obligation can be triggered merely by serving a dependent program over a network — not only by distributing it — which is directly relevant given PriorisMCP's `streamable-http`/`http` transport (see [Security → PriorisMCP's own HTTP ingress surface](05-security.md#priorismcps-own-http-ingress-surface)).
