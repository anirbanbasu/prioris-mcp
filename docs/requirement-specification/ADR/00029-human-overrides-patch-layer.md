---
status: accepted
date: 2026-08-13
---

# ADR-00029: Human overrides are an override-record patch layer over an immutable base graph

## Context

`structural`/`llm_extracted` content needs a way for a human to correct or veto it, without breaking two already-settled mechanisms that depend on `structural`/`llm_extracted` staying pure and machine-derived: content-equality diffing (trusts `structural` re-parse output as pure, deterministic re-derivation) and cascade eligibility (depends on `structural`/`llm_extracted` staying cleanly regenerable-and-disposable).

## Decision

`structural` and `llm_extracted` write an immutable base graph, never mutated or deleted by human action directly. `human_annotated` is a separate, small set of override records of exactly two kinds — `add` (optional `overrides: [id, ...]` list of base records it supersedes) and `reject` (`targets: [id, ...]`, a pure veto, no content payload) — resolved against the base graph at query time via the same live-resolution mechanism `get_nodes`/`get_edges`/`neighbors` and `GraphAlgorithmsBackend` use (see [ADR-00028](00028-graph-algorithms-networkx-not-engine.md)).

## Alternatives considered

- **Mutating overrides directly into the base layer, with stored inverse transformations for undo** — rejected: would make "the graph" always already resolved, no separate resolution step needed anywhere — but at the cost of re-coupling the override layer to the base layer's mutable state. A human-edited base row would make an unchanged re-parse look like a content change, reintroducing the exact false-churn failure mode content-equality diffing exists to avoid. A human edit baked into a `structural` row would also either get silently destroyed by document-deletion cascade, or need a new "this row secretly carries protected content" marker — the same discriminator-field shape rejected twice elsewhere in this document (provenance naming, node kind).
- **Promoting an existing `structural`/`llm_extracted` entry in place** — overwriting its provenance field to `human_annotated`, with a `promoted_from` snapshot enabling reversal ("demotion"). An early design draft, reversed once it became clear human annotation needs to include **removing** nodes/edges the LLM or structural layer surfaced, not just correcting or re-tagging them — at which point there's no single entry being "promoted," and `promoted_from` has no coherent job left to do.

## Consequences

Undo is trivial and needs no dedicated mechanism: deleting an override record (`add` or `reject`) restores the base record's original effective state automatically, since the base was never touched in the first place. Query resolution falls out directly: for any base record, a `reject` excludes it, an `add.overrides` supersedes it, otherwise the base record stands as-is — which is also why `structural`/`llm_extracted` remain sufficient as a standalone source of truth when no human override exists at all. Cascade mechanics stay clean since overrides only ever reference base ids by list membership: pruned from every `add.overrides`/`reject.targets` list on cascade-delete, a `reject` whose `targets` empties is itself deleted, an `add` whose `overrides` empties survives as an ordinary standalone addition.

## Referenced from

- [Graph search → Graph algorithms: implemented independently via NetworkX, not delegated to the engine](../search/03-graph-search.md#graph-algorithms-implemented-independently-via-networkx-not-delegated-to-the-engine)
- [Graph search → Human overrides: a patch layer on top of the base graph, not a promotion tier](../search/03-graph-search.md#human-overrides-a-patch-layer-on-top-of-the-base-graph-not-a-promotion-tier)
