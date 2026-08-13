---
status: accepted
date: 2026-08-01
---

# ADR-00007: Notes export as a resource, not a tool

## Context

A note's content needs to be exportable for external use (e.g. writing it into an Obsidian vault). The question is whether that's a tool (mirroring `research_notes_search`'s shape) or an MCP resource.

## Decision

`notes://{note_id}/export` is a **resource**, not a tool, and takes only a note's id — not `search`'s filter set.

## Alternatives considered

- **A tool mirroring `search`'s filter parameters** — rejected: would duplicate `search`'s query logic in a second tool for no reason, since export only ever needs one already-identified note, not a filtered set.
- **A tool that writes the exported content directly to a host path** — rejected as a security boundary, not just an API-shape preference: per [Security → Local filesystem access means access to the caller's own content, not the server's disk](../05-security.md#local-filesystem-access-means-access-to-the-callers-own-content-not-the-servers-disk), the server must not write files to an arbitrary host path itself.

## Consequences

Export follows the split already established elsewhere in this codebase: tools (`research_search_fetched`/`research_list_fetched`) *find* things, and a resource (`research://{provider}/{identifier}/{format}/markdown`) *reads* one. The caller (or MCP client) is responsible for writing the returned content to disk itself.

## Referenced from

- [Architecture → NotesBackend → Export is a resource, not a tool](../01-architecture.md#export-is-a-resource-not-a-tool)
