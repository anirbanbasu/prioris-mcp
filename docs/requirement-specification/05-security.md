---
icon: lucide/shield
---

# Security

This page covers security requirements — another cross-cutting quality alongside [Non-functional requirements](04-non-functional-requirements.md), but broken out into its own page rather than folded in there, since v1's security surface is concrete enough (and distinct enough in kind from concurrency) to warrant its own visibility rather than being one subsection among others.

v1 targets unauthenticated, open-access providers only (see [SRS overview → Scope](index.md#v1)), so this page is **not** about authentication or credential handling — it's about the concrete risks that exist even without any auth: an MCP client-supplied identifier driving an outbound network request, and externally-sourced content flowing into a parser.

## Untrusted identifiers must not drive unconstrained outbound requests

[Architecture → Identifier routing](01-architecture.md#identifier-routing-grouping-level) already requires DOI resolution to fail with an "unsupported provider" error rather than fall back to scraping an arbitrary landing page — that decision was made for terms-of-use reasons, but it also happens to be a security boundary: `research_resolve_identifier` takes a caller-supplied identifier and, for a DOI, follows a redirect (via `doi.org`/Crossref) to a URL it does not control in advance.

This must be implemented as a strict allowlist on the resulting domain (arxiv.org, europepmc.org/NCBI PMC) checked **before** any further request is made against it, not as "fetch the landing page, then decide what to do with it." A caller-influenced identifier must never be able to cause PriorisMCP to issue a request to an arbitrary host — including internal/private network addresses — as a side effect of resolution. The existing functional requirement (fail closed with "unsupported provider") already produces the right behaviour; this page states the security rationale for why that must hold even if the functional requirement is later relaxed (e.g. to add a new supported provider) — any addition must extend the allowlist deliberately, not open resolution up to whatever a redirect happens to return.

## Fetched content is untrusted input to `parse_full_text`

Full text persisted by `fetch_full_text` originates from external sources and is later fed into `parse_full_text`. Even though v1's providers (arXiv, Europe PMC) are trusted in the sense of being deliberately chosen, documented APIs, the documents they serve are third-party content (uploaded by their own authors/publishers) that PriorisMCP does not control the shape of.

`parse_full_text` must treat this content as untrusted input: malformed, unusually large, or pathological documents (e.g. a PDF crafted to be maximally expensive to parse) must fail as a bounded, typed error rather than crash the server process or hang it indefinitely. This is a robustness requirement on the parsing path specifically, distinct from the general error-handling already described for missing content in [Architecture → `parse_full_text`](01-architecture.md#parse_full_text).

### A bounded per-call failure is not sufficient on its own

The JATS-XML-to-Markdown path (`JatsXsltMarkdownBackend` in `src/prioris_mcp/parsers/jats_xslt.py`) runs its XSLT transform in a worker thread and enforces the bound above by abandoning that thread if it overruns the deadline, rather than waiting for it — Python offers no way to forcibly stop a thread already running native (libxslt) code, and abandoning it is what lets the *calling* `parse_full_text` invocation still fail cleanly at the bound instead of hanging past it. The abandoned thread itself keeps running to completion in the background, consuming a CPU core and holding its partially-built result tree in memory for however long the pathological document actually takes — which is unbounded.

A single such call is harmless. But nothing in a per-call bound stops repeated calls — against pathological documents, arriving faster than each one naturally finishes — from accumulating unboundedly many of these abandoned-but-still-running transforms concurrently, each holding a CPU core and memory: an aggregate resource-exhaustion risk distinct from, and not closed by, the per-call timeout above.

This must be closed by bounding how many JATS transforms are ever *actually executing* at once, independent of how many `parse_full_text` calls have been made or abandoned: a fixed-size gate held for a transform's true lifetime (acquired before the CPU-bound work starts, released only when it actually finishes, in a `finally`), not one tied to the calling task's own cancellation — `anyio`'s `to_thread.run_sync(limiter=...)` looks like it would do this, but its capacity limiter is released as soon as the *caller* is cancelled/abandons, not when the worker thread actually finishes, so it does not bound concurrently-running abandoned threads at all. Calls beyond the cap block waiting for a slot rather than starting a new transform outright, and are themselves still subject to the same per-call bound above — so a caller that can't acquire a slot in time still fails as a clean, typed error rather than queuing indefinitely.

The cap (`PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS` — see [Configuration](../02-configuration.md)) is configurable, but is capped at the host's CPU count regardless of configuration: this is CPU-bound native work, so oversubscribing beyond available cores only makes the worst case worse without any offsetting throughput benefit.

## Authenticated sources are explicitly deferred, not silently assumed

Credential/secret handling for authenticated sources (e.g. Semantic Scholar) is out of scope for v1 (see [SRS overview → Out of scope for v1](index.md#out-of-scope-for-v1)). This page notes it explicitly so it isn't forgotten: adding an authenticated provider later will need its own security requirements (credential storage, no secrets in logs or cached responses, per-source scoping) written before that provider is built, not assumed to fall out of the existing unauthenticated design.

## PriorisMCP's own HTTP ingress surface

Everything above concerns PriorisMCP calling *out* to arXiv/Europe PMC. v1 also serves its own MCP interface over `streamable-http`/`http` (`PRIORIS_MCP_TRANSPORT`, alongside `stdio` — see [SRS overview → Scope](index.md#v1)), which has an inbound surface of its own, distinct from anything above:

- **No authentication/authorization on the HTTP transport in v1.** Same principle as [Authenticated sources are explicitly deferred](#authenticated-sources-are-explicitly-deferred-not-silently-assumed) above, applied to PriorisMCP's own ingress rather than the sources it queries: v1 assumes the HTTP transport runs within a trusted local/network context — an agent invoking PriorisMCP over `stdio` is the primary expected path — not exposed to the public internet. Real authentication is future work, stated explicitly so it isn't mistaken for an oversight.
- **Default bind address is loopback, and must stay that way.** `PRIORIS_MCP_HOST` already defaults to `localhost` (see `src/prioris_mcp/__init__.py`); binding a wider interface is a deliberate operator override via that same variable, never the shipped default.
- **CORS defaults to localhost origins, not the wildcard.** `PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS` defaults to `["http://localhost", "http://127.0.0.1"]` (see `src/prioris_mcp/__init__.py`), not `["*"]`. A wildcard CORS origin means any webpage's JavaScript — regardless of that page's own origin — can read this server's responses if it can reach it over the network; CORS is what makes the response *readable* to the page's script, not a network-reachability control. Restricting the default to explicit localhost-family origins closes this off for arbitrary/attacker origins while still allowing legitimate locally-served tools once their specific origin is added. The wildcard remains available as an explicit override for tools that don't fit a fixed localhost-origin allowlist (e.g. the MCP Inspector, which currently requires it) — that must be a deliberate operator choice made via the environment variable before launching such a tool, not the out-of-the-box behaviour.

## Egress through a organisational HTTPS-inspecting proxy

The outbound requests described above ([Untrusted identifiers](#untrusted-identifiers-must-not-drive-unconstrained-outbound-requests), and the arXiv/Europe PMC fetches behind `fetch_metadata`/`fetch_full_text`) all go through the single `httpx.AsyncClient` shared across providers (see `PriorisMCP.__init__` in `src/prioris_mcp/server.py`). Operators running PriorisMCP behind a organisational HTTPS-inspecting proxy — one that terminates TLS with its own, typically self-signed, certificate authority — need two things from that client, both of which httpx already provides without any PriorisMCP-specific code:

- **Proxy routing.** `httpx.AsyncClient` defaults to `trust_env=True`, so it already honours the standard `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` environment variables for routing outbound requests through a proxy.
- **Trusting the proxy's CA.** Since `verify=True` (the default — see below) uses Python's default SSL context, setting the standard `SSL_CERT_FILE` (a single PEM bundle) or `SSL_CERT_DIR` (a directory of certificates) environment variable to include the proxy's root CA is sufficient for outbound HTTPS requests to be verified against it, the same as any other trusted CA.

`test_outbound_http_client_trusts_env_for_proxy_and_ca_bundle` (`tests/test_server.py`) guards `trust_env` staying on, since it's what makes both of the above work.

`PRIORIS_MCP_UNVERIFIED_HTTPS` (see [Configuration](../02-configuration.md)) is a separate, narrower escape hatch for when neither applies — e.g. a proxy whose CA can't be added to the environment's trust store. It disables HTTPS certificate verification entirely for every outbound request, not just those behind the proxy, so it must be used sparingly: development/testing only, never as a default or a substitute for installing the proxy's CA. Enabling it logs a `WARNING` at server startup so the reduced security posture is visible in operational logs, not a silent config change.
