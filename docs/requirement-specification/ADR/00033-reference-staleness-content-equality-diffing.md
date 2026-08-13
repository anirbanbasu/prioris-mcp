---
status: accepted
date: 2026-08-13
---

# ADR-00033: Reference staleness on re-extraction: content-equality diffing for `structural`, wipe-and-refresh for `llm_extracted`

## Context

Reconciling entry ids across re-extraction/re-parse passes is complicated by `human_annotated` override records referencing base-record ids by list membership (`add.overrides`, `reject.targets`) — unlike Vector search's `chunk_id` ([ADR-00026](00026-chunk-id-uuid-not-heading-or-offset.md)), which needs no cross-pass stability since nothing references it, ids here must survive a re-run whenever the underlying content is genuinely unchanged, or overrides silently orphan.

## Decision

Split by layer, since drift-risk profiles genuinely differ. **`structural`** uses entry-level content-equality diffing: each new entry is compared against the existing stored set for that document+type by exact content match (not id), keeping its existing id on a match — untouched, no delete/reinsert — so any override referencing it keeps applying with zero staleness in the common unchanged case; comparison normalizes only Unicode NFC and whitespace trim/collapse, deliberately representation-only. An entry no longer present is removed via cascade mechanics, but its affected override is surfaced `needs_review` rather than silently pruned. **`llm_extracted`** re-extraction is never incremental or reconciled at the entry level: a deliberate, document-scoped wipe-and-refresh — every `llm_extracted` node/edge for that document is removed and replaced with a fresh extraction, all new ids, every affected override flagged `needs_review`.

## Alternatives considered

- **Delete-by-provenance-scope-then-insert, minting fresh ids on every re-parse** (mirroring `chunk_id`'s mint-fresh-per-pass pattern) — rejected: unlike vector/FTS chunks, nothing here can tolerate ids not surviving a pass — fresh ids on every re-parse would silently orphan every override touching that document on its very first re-parse.
- **A content-hash-derived id**, stable across passes when content is unchanged — works for `structural` (deterministic given the same inputs) but breaks for `llm_extracted`, where paraphrasing/model drift between passes is expected, not exceptional — a hash would treat "the same fact, re-extracted, worded slightly differently" as a brand-new entry every time.
- **Fuzzy/similarity-based re-linking** (edit-distance shortlisting plus topological first-hop-neighbour comparison, or embedding similarity) — explored at length and rejected: the more aggressively a matcher tries to reconcile, the more likely a **false merge** — and a false merge is silent, since a successful (wrong) match produces no signal at all. Sharpest exactly when it matters most: a weaker re-extraction model (genuine concept drift, not just rewording) could produce output that coincidentally matches an old, correct, human-reviewed entry, silently binding a human's override to content it was never meant to apply to.
- **Closing `structural`'s remaining near-miss gaps via case-folding, punctuation-stripping, or Unicode compatibility folding (NFKC)** — rejected even though they'd also close gaps like `apple`→`Apple` recasing: unlike NFC/whitespace-collapse, these discard actual information rather than choose among equivalent encodings, reopening the same false-merge risk at smaller scale.

## Consequences

An override silently reattaching to the wrong entry is worse than an override going stale, because it fails silently — so no heuristic matching is used for either layer. This resolves the common case for free (`structural` re-parses over an unchanged document leave existing overrides untouched) while accepting a coarser but honest "review everything touched" for `llm_extracted`, over a finer-grained mechanism that can't be made safe against genuine model drift. A re-parse that recases `apple` to `Apple` is therefore still treated as an unrelated removal-plus-addition, and any override touching it still gets flagged `needs_review` — an accepted, conscious cost. PDF-extraction artefacts like cross-line hyphenation are explicitly out of scope for this comparison — the parser's job to get right upstream, not the diffing layer's to guess at.

## Referenced from

- [Graph search → Reference staleness on re-extraction: content-equality diffing for structural, full wipe-and-refresh for llm_extracted](../search/03-graph-search.md#reference-staleness-on-re-extraction-content-equality-diffing-for-structural-full-wipe-and-refresh-for-llm_extracted)
