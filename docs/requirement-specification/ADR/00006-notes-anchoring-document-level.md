---
status: accepted
date: 2026-08-01
---

# ADR-00006: Notes anchoring — document-level, not span-level

## Context

A note needs to be attached to a document. `manifest.sqlite` already has a `leaf`/`chunk` span model — the question is whether a note should anchor to a specific span inside it (a durable reference into a particular row) or only to the document as a whole.

## Decision

A note is keyed by `(provider, canonical_identifier, format)` — document-level identity, not a span anchor.

## Alternatives considered

- **Span-level anchoring** (a durable reference into a specific `manifest.sqlite` row) — rejected on four independent grounds:
    - **No stable row identity survives a re-parse.** `manifest.sqlite` rows are wholesale-replaced on every re-parse; nothing in `StorageBackend`'s existing design ever needed a *durable, cross-time* external reference into a manifest row before, because every current consumer either re-derives its query fresh each call or is itself explicitly disposable. Not fixable by choosing a different primary-key type (e.g. a UUID instead of an autoincrement `id`) — a re-parse regenerates fresh rows regardless of what identifies them.
    - **Even a workably-unique row tuple is emergent, not declared.** A genuinely unique tuple turns out to be `(format, scheme, kind, span_start)`, and that uniqueness is a property of the one v1 chunk-detection algorithm, not a database constraint — too fragile a foundation for a durable anchor.
    - **Precision isn't the same as reliability.** Even with a pinned, version-immutable identifier, PDF OCR is not fully deterministic — a one-character difference upstream shifts every later span offset, silently invalidating a span anchor even when the source content never changed.
    - **The better long-term answer to "find notes relevant to this passage" is semantic, not positional.** A similarity search between a note's text and the chunk currently being read (see [Vector search](../search/02-vector-search.md)) finds relevant notes regardless of exact wording or position — something exact span-matching could never do even when it resolves correctly.

## Consequences

Positional information isn't dropped entirely: a note may carry `anchors`, unresolved and unenforced positional hints (page/heading/paragraph plus a W3C Web Annotation-style text-quote selector) that a human (or an agent acting on explicit user input) can use to relocate a passage by eye — never validated or resolved against `manifest.sqlite`. This decision also directly motivated pursuing [Vector search](../search/02-vector-search.md) as the better long-term answer to passage-relevant note discovery.

## Referenced from

- [Architecture → Anchoring: document-level, not span-level](../01-architecture.md#anchoring-document-level-not-span-level)
