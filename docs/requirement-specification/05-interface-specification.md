---
icon: lucide/plug
---

# Interface specification

_This page is a placeholder. It is scoped but not yet written._

## Scope

[Functional requirements](03-functional-requirements.md) states what each MCP tool and resource must accept and guarantee, in behavioural terms — deliberately without literal parameter names or JSON schemas. This page is where those become concrete: the exact wire-level input/output contract for every tool, resource, and prompt PriorisMCP exposes over MCP — parameter names and types, `format` enum values per provider/item, resource URI template variables, and the shape of error responses (e.g. the "not found" and "unsupported provider" errors described in [Architecture](01-architecture.md) and [Functional requirements](03-functional-requirements.md)).

This is a distinct concern from functional requirements in the same sense classic requirements-engineering practice (e.g. IEEE 830) separates functional requirements from external interface requirements: one states what the system must do, the other states the literal shape of the boundary a caller interacts with. MCP tools/resources/prompts are exactly that boundary.

## Why this is deferred

Exact parameter names and enum values depend on details only the real arXiv and Europe PMC APIs can settle — their actual response fields, pagination conventions, and identifier formats. Writing schemas now, before those APIs have been read during implementation, risks the SRS asserting details that turn out wrong and then need chasing down later. This page is populated once that implementation groundwork has happened.

## Next

- [Test specification](06-test-specification.md) — depends on this page existing, since test cases need concrete input/output shapes to assert against.
