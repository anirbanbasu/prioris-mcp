---
status: accepted
date: 2026-07-20
---

# ADR-00005: Local-filesystem source identity via content hashing

## Context

[`resolve_identifier`](../01-architecture.md#resolve_identifier)'s job for arXiv and Europe PMC is pinning a possibly-mutable identifier to an immutable version *before* it's used as a storage key — this works because each provider is itself the authority on what "the current version" means. The [local filesystem source](../01-architecture.md#local-filesystem-source) has no such authority: the same conceptual document can arrive with different bytes on different calls (the caller can send an edited or replaced file), with nothing external asserting which version is canonical.

## Decision

`fetch_full_text` hashes the caller-sent bytes (SHA-256) on every call, and that hash — not any path or filename — becomes the canonical identity used for the storage key, exactly the role a version-pinned arXiv ID plays for that provider. See [Storage → Content-hash canonicalisation for the local filesystem source](../storage/01-document-storage.md#content-hash-canonicalisation-for-the-local-filesystem-source) for the full storage-key mechanics.

## Alternatives considered

- **The caller-supplied filename or path** — rejected: no external authority asserts that a given filename/path always refers to the same bytes over time; the caller can send an edited or replaced file under an unchanged name, and PriorisMCP would have no way to detect it.

## Consequences

Hashing is cheap (a local base64 decode, not a network round-trip) and runs unconditionally on every `fetch_full_text` call rather than being gated behind a force-refetch flag. A hash match skips the storage `write`; a hash mismatch persists the new content under its own new identity, leaving any identifier a caller was previously given for the old content valid and unaffected — the same non-destructive-update guarantee arXiv's own versioning already provides.

## Referenced from

- [Architecture → Local filesystem source](../01-architecture.md#local-filesystem-source)
