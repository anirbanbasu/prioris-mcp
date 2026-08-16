---
status: accepted
date: 2026-07-20
---

# ADR-00002: HTML/JATS parsing pipeline — `html-to-markdown` + vendored XSLT over a third-party MathML library

## Context

[`parse_full_text`](../01-architecture.md#parse_full_text) needs a `ParserBackend` for two source formats that turn out to share most of a solution: arXiv's HTML full text, and Europe PMC's JATS XML. JATS additionally carries MathML-encoded formulas and a heading structure that doesn't map cleanly onto a fixed nesting-depth scheme.

## Decision

`html-to-markdown` converts arXiv's HTML directly. Europe PMC's JATS XML is transformed to HTML first via a vendored NCBI XSLT stylesheet (`JatsXsltMarkdownBackend`), then run through the same `html-to-markdown` backend — reuse, not a second HTML converter. That vendored stylesheet carries two purpose-built fixes beyond its original vendor behaviour, both implemented as more vendored XSLT: a dynamic, ancestor-anchored heading-depth calculation, and a MathML-to-LaTeX conversion pass that prefers an author-provided `tex-math` alternative when JATS supplies one alongside MathML for the same formula.

## Alternatives considered

- **A different default HTML-to-Markdown library** for arXiv — `html-to-markdown` was specifically selected for correct `rowspan`/`colspan` table handling, which alternatives checked did not reliably provide.
- **The stylesheet's original fixed 3-level heading mapping** — rejected: it mis-levels any heading nested deeper than a subsection, which JATS documents routinely have.
- **A third-party MathML-to-LaTeX conversion library** — rejected: the actively-available candidates checked at the time were unmaintained, an unacceptable risk for a conversion step every JATS document with formulas depends on.

## Consequences

The two XSLT fixes (heading-depth, MathML-to-LaTeX) become part of this project's own maintenance surface rather than a third-party dependency's — a deliberate tradeoff of more code owned here for not depending on an unmaintained library. JATS math fidelity favours an author-provided `tex-math` source over any MathML reconstruction wherever both are present, since it's strictly more faithful.

## Referenced from

- [Architecture → `parse_full_text`](../01-architecture.md#parse_full_text)
