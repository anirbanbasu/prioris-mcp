---
status: accepted
date: 2026-08-13
---

# ADR-00029: JATS transform concurrency is bounded by a dedicated gate, not `anyio`'s `to_thread.run_sync(limiter=...)`

## Context

`JatsXsltMarkdownBackend` (`src/prioris_mcp/parsers/jats_xslt.py`) runs its XSLT transform in a worker thread and enforces a per-call deadline by abandoning that thread if it overruns, rather than waiting for it — Python offers no way to forcibly stop a thread already running native (libxslt) code. The abandoned thread keeps running to completion in the background regardless, consuming a CPU core and holding its partially-built result tree in memory for however long the pathological document actually takes. A per-call bound alone doesn't stop repeated calls against pathological documents, arriving faster than each one naturally finishes, from accumulating unboundedly many of these abandoned-but-still-running transforms concurrently.

## Decision

Bound how many JATS transforms are ever actually *executing* at once via a fixed-size gate held for a transform's true lifetime — acquired before the CPU-bound work starts, released only when it actually finishes, in a `finally` — independent of how many `parse_full_text` calls have been made or abandoned.

## Alternatives considered

- **`anyio`'s `to_thread.run_sync(limiter=...)`** — looks like it would provide exactly this bound, since it's `anyio`'s own built-in concurrency limiter for thread-offloaded work. Rejected once its actual release semantics were checked: its capacity limiter releases as soon as the *caller* is cancelled or abandons the call, not when the worker thread itself actually finishes — so it does not bound concurrently-running abandoned threads at all, the exact failure mode this gate exists to close.

## Consequences

The cap (`PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS`) is configurable but capped at the host's CPU count regardless of configuration, since oversubscribing this CPU-bound native work beyond available cores only makes the worst case worse. Calls beyond the cap block waiting for a slot rather than starting a new transform outright, and are themselves still subject to the same per-call bound — so a caller that can't acquire a slot in time still fails as a clean, typed error rather than queuing indefinitely.

## Referenced from

- [Security → A bounded per-call failure is not sufficient on its own](../05-security.md#a-bounded-per-call-failure-is-not-sufficient-on-its-own)
