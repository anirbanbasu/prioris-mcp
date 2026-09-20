---
status: accepted
date: 2026-09-20
---

# ADR-00037: Metadata merge-on-write raises on conflicting values, not silent overwrite or merge

## Context

`upsert_pointer`, `update_concept`, and `update_edge` can all write `metadata` onto a node/edge that may already carry metadata from an earlier write — by a different concept linking the same `Pointer` ([ADR-00033](00033-graph-node-shape-pointer-concept-tables.md)), or by a different writer (human vs. LLM-suggested) updating the same edge ([ADR-00034](00034-graph-edge-shape-generic-relationship-table.md)). A second write's `metadata` needs a defined merge behaviour against what's already there.

## Decision

Per-key merge with conflict detection, applied uniformly wherever metadata is written onto an existing node or edge. For each key present in both the existing and new metadata: identical values are a no-op; differing values raise `MetadataConflictError` (new, in `errors.py`, mapping to a new `metadata_conflict` error code, following the existing `NotFoundError`/`FormatUnavailableError` pattern) naming the conflicting key(s), rather than silently picking a winner. Keys unique to either side pass through untouched.

## Alternatives considered

- **Silent overwrite** (new value always wins) — rejected: a second writer's edit could silently destroy a first writer's metadata (e.g. overwriting `provenance: "human_annotated"` with `provenance: "llm_discovered"` for the same key) with no visibility into what was lost.
- **Silent merge** (new value wins per-key, no error) — has the identical silent-loss problem as overwrite for any key both sides set; rejected for the same reason.
- **Whole-dict replace** (new `metadata` entirely replaces old, no merge at all) — rejected: would force every writer to first read the existing metadata and republish the union just to add one key, defeating the point of an open, incrementally-extensible metadata bag.

## Consequences

A caller hitting `MetadataConflictError` must re-issue the write with an explicitly resolved value for the conflicting key(s) — more round trips than silent merge in the conflicting-key case, but no metadata is ever lost without the caller knowing. This is the same shape of tradeoff as any optimistic-concurrency-style conflict signal: correctness over convenience.

## Referenced from

- [Graph search → Metadata merge-on-write](../search/03-graph-search.md#metadata-merge-on-write-conflict-raises-not-silent-overwrite-or-merge)
