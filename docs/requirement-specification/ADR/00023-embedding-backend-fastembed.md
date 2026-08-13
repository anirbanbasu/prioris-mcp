---
status: accepted
date: 2026-08-12
---

# ADR-00023: Default `EmbeddingBackend` is `fastembed`, not `sentence-transformers`

## Context

`EmbeddingBackend` needs a default implementation. `sentence-transformers` is the more commonly reached-for library for local embedding generation. Its model dimensionality also needed a starting default — some embedding APIs (OpenAI's `ada-002`, `text-embedding-3-large`) use 1536 dimensions, raising the question of whether that should be treated as a target.

## Decision

The default `EmbeddingBackend` implementation is `fastembed` (Qdrant's library, MIT-licensed, ONNX-based, no PyTorch dependency), with default model `BAAI/bge-small-en-v1.5` (384 dimensions, English, `fastembed`'s own default model). The model name is exposed as `PRIORIS_MCP_EMBEDDING_MODEL`, left as an open string rather than `OneOf`-validated against a maintained allowlist.

## Alternatives considered

- **`sentence-transformers`** — rejected: the same reasoning that favoured `sqlite-vec`'s precompiled-wheel footprint over a heavier general-purpose ML stack applies here — `sentence-transformers` pulls in the full PyTorch/transformers stack, a materially heavier install for a project that has otherwise stayed light.
- **1536 dimensions as a design target** — rejected: it's an artifact of OpenAI's specific API models, not a property well-established local/open embedding models converge on. Dimension is a downstream consequence of whichever model ends up configured, not an independent knob.

## Consequences

A user needing multilingual support sets `PRIORIS_MCP_EMBEDDING_MODEL` to something like `intfloat/multilingual-e5-large` (1024 dimensions) without a new `EmbeddingBackend` implementation — `fastembed` already errors on an unrecognized model name, so there's no need for this project to duplicate that validation. `sqlite-vec`'s `vec0` fixing one dimension per table follows directly from treating dimension as a model consequence, not an independent knob.

## Referenced from

- [Vector search → Default `EmbeddingBackend`: `fastembed`, model configurable via environment variable](../search/02-vector-search.md#default-embeddingbackend-fastembed-model-configurable-via-environment-variable)
