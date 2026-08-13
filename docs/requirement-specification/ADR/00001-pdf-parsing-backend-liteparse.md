---
status: accepted
date: 2026-07-20
---

# ADR-00001: PDF parsing backend — `liteparse` over `docling`

## Context

[`parse_full_text`](../01-architecture.md#parse_full_text) needs a `ParserBackend` that converts fetched PDF full text (arXiv, and the [local filesystem source](../01-architecture.md#local-filesystem-source)) into Markdown. Several viable PDF-to-Markdown libraries exist, differing mainly in fidelity of structure recovery versus install weight — see [Non-functional requirements → Dependency selection](../04-non-functional-requirements.md#dependency-selection) for the general criteria this and every other backend choice is held to.

## Decision

`LiteParsePdfBackend`, backed by `liteparse`.

## Alternatives considered

- **`docling`** (and comparable heavier ML-based document-parsing libraries) — rejected specifically because it pulls in a PyTorch dependency, a materially heavier install footprint than a project that has otherwise stayed light on dependencies wants to carry for its default backend.

## Consequences

v1's PDF path has no PyTorch dependency. Higher-fidelity structure recovery on complex layouts (tables, multi-column academic formatting) is a known, accepted ceiling of `liteparse` relative to `docling`-class tools — not solved here, but left as a named future option: [SRS overview → Out of scope for v1](../index.md#out-of-scope-for-v1) already names a slower, more sophisticated PDF backend as a future addition, swappable in via the same pluggable `ParserBackend` interface without changing the interface itself.

## Referenced from

- [Architecture → `parse_full_text`](../01-architecture.md#parse_full_text)
