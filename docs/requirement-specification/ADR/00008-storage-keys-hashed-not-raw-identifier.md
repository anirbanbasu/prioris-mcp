---
status: accepted
date: 2026-07-20
---

# ADR-00008: Storage keys are hashed, not built from the raw identifier

## Context

Persisted content needs a filesystem path derived from **provider + canonical item identifier**. An identifier is not safe to use directly as a filename or path segment: DOIs contain `/` (e.g. `10.1000/xyz123`), identifiers can contain arbitrary characters, and filenames built from external input are a path-traversal risk if anything ever produces a malformed or adversarial-looking identifier. Enumerating and escaping every unsafe character is easy to get subtly wrong; filename-length limits are a further, separate constraint.

## Decision

The top-level storage key — the **document-hash** — is derived by **hashing** `(provider, canonical identifier)` (a SHA-256 hex digest) rather than encoding the identifier into the path. `format` is deliberately excluded from this hash: it's a plain, literal path segment (`pdf`, `html`, ...) nested under the document-hash directory.

## Alternatives considered

- **Encoding/escaping the raw identifier directly into the path** — rejected: enumerating and escaping every character that's unsafe in a filename (path separators, length limits, adversarial-looking input) is easy to get subtly wrong, and a hashed key removes the character-safety and path-traversal concerns entirely rather than mitigating them case by case.
- **Hashing `format` together with `(provider, identifier)`** (one independently-hashed location per artefact triple) — rejected: would scatter every format fetched for the same document across unrelated hash locations instead of landing under one shared parent directory; unnecessary anyway, since `format` is drawn from a small, fixed, already filesystem-safe vocabulary, unlike an externally-supplied identifier.

## Consequences

The document-hash is always a fixed-length, filesystem-safe string. Human-readability is preserved separately: each format directory has its own `metadata.jsonl`, and the top-level `catalogue.sqlite` indexes every entry across the whole store — so nothing needs to be recovered by inspecting the hash itself.

## Referenced from

- [Storage → Storage keys are hashed, not built from the raw identifier](../storage/01-document-storage.md#storage-keys-are-hashed-not-built-from-the-raw-identifier)
