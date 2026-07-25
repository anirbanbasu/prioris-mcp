---
icon: lucide/gauge
---

# Non-functional requirements

This page covers cross-cutting qualities that apply across capabilities and providers, rather than the behaviour of any one tool — the counterpart to [Functional requirements](03-functional-requirements.md). v1 scopes this to **concurrency**, since it's the one cross-cutting property the SRS so far has been silent on despite it directly constraining the design in [Architecture](01-architecture.md) and [Storage](02-storage.md). Other NFR categories (performance targets, availability, observability, ...) are future work, not addressed yet.

## Concurrency

An MCP client (an LLM) may legitimately invoke variations of the same tool in parallel — for example, several `research_arxiv_search` calls with different keywords, or several `research_europepmc_fetch_metadata` calls for different identifiers, issued together rather than one at a time. v1 must support this without corrupting state or silently violating a source's terms of use.

### Rate limiting must serialise, not just gate

[Architecture → Caching and rate limiting](01-architecture.md#caching-and-rate-limiting) states each provider's outbound rate limit (arXiv: documented 1 request per 3 seconds; Europe PMC: the same, self-imposed — see [Functional requirements](03-functional-requirements.md)). A check that only asks "has 3 seconds passed since the last request?" is correct for sequential calls but insufficient under concurrency: several parallel tool invocations against the same provider can each see "yes, enough time has passed" and fire together, breaching the limit.

Each provider must therefore serialise its own outbound requests through a single queue (or equivalent), so that concurrent tool invocations against that provider are admitted to the network one at a time, spaced according to its rate limit, rather than racing against a shared check. This applies within a provider; it does not require coordinating across providers, since the rate limit itself is per-source.

### Storage must de-duplicate in-flight work, not just completed work

[Storage → `StorageBackend`](02-storage.md#storagebackend) already de-duplicates *completed* downloads and parses via `exists`. That's insufficient under concurrency: if two calls for the same `(provider, canonical identifier, format)` key arrive close together, both can observe `exists` as false before either has finished `write`-ing, and both proceed — a duplicate concurrent download (for `fetch_full_text`) or a duplicate concurrent parse (for `parse_full_text`) for the same key.

`StorageBackend` (or the layer calling it) must guarantee that only one fetch and only one parse is ever in flight for a given key at a time: a second concurrent request for a key already being fetched or parsed must wait for that in-flight operation to complete and be served its result, rather than starting a redundant one. This is an in-flight lock, distinct from `exists` (which only reflects already-completed work).

## Next

[Security](05-security.md) covers the other cross-cutting requirements category for v1. [Interface specification](06-interface-specification.md) and [test specification](07-test-specification.md) exist as pages but are deliberately deferred.
