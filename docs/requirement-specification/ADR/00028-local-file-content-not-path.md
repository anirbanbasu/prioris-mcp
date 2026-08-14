---
status: accepted
date: 2026-08-13
---

# ADR-00037: Local-filesystem access is via caller-sent content, not a server-side path

## Context

Earlier revisions of the [local filesystem source](../01-architecture.md#local-filesystem-source) accepted a caller-supplied path, resolved it against an operator-configured root directory, and read the file directly off the server process's own disk. That design implicitly assumed the MCP client and the server process share a filesystem.

## Decision

`research_localfile_fetch_full_text` takes the caller's file **content**, base64-encoded (`content_base64`), rather than a path.

## Alternatives considered

- **Caller-supplied path resolved against an operator-configured root directory** (the earlier design) — rejected: that assumption is true for `stdio` transport, where client and server share a process's filesystem, but not for `streamable-http`/`http`, where "local" from the client's perspective and "local" from the server's perspective are unrelated filesystems. A path the client intended to reference on its own machine would instead resolve (or fail to resolve, or worse, silently resolve to an unrelated file) on the server's machine, bounded by a root directory the client has no relationship to.

## Consequences

Removes the path-containment problem entirely rather than hardening it further: there is no server-side path to resolve, so there is nothing for a `..` segment or a symlink to escape. The remaining risk shifts to bounding how much content a caller can send and validating what it actually is (size caps, PDF content-sniffing) — a different, already-covered concern. The same principle extends to `notes://{note_id}/export`: it returns a note's file representation for the caller to write itself, rather than the server writing to some destination path, since a server-chosen filesystem path is equally meaningless once client and server don't share one.

## Referenced from

- [Security → Local filesystem access means access to the caller's own content, not the server's disk](../05-security.md#local-filesystem-access-means-access-to-the-callers-own-content-not-the-servers-disk)
- [Security → Notes export does not write files](../05-security.md#notes-export-does-not-write-files)
