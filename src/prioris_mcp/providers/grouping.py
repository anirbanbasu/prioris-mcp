"""Grouping-level storage isolation.

See docs/requirement-specification/01-architecture.md#provider-groupings. `SearchIndex`,
`NotesBackend`, and `StorageBackend` are each single, corpus-wide instances with no awareness of
which provider grouping (`ResearchPublicationProvider`, the only one that exists today) a document
came from. `grouping_dir` is the seam a future second grouping (e.g. `PatentProvider`) attaches
through: each backend already takes its storage location as an explicit constructor argument, so
isolating a grouping is a matter of constructing it against a different directory, not an
interface change.

`DEFAULT_GROUPING` mirrors the `research_<domain>_<verb>` MCP tool-name prefix, not a general
label - see docs/requirement-specification/101-debate-graph-search.md's "MCP tool wiring" sections.
"""

from pathlib import Path

DEFAULT_GROUPING = "research"


def grouping_dir(base: Path, grouping: str) -> Path:
    """Resolve `base` to a grouping-scoped subdirectory, except for `DEFAULT_GROUPING`.

    Returns `base` unchanged for `DEFAULT_GROUPING`, so today's on-disk layout - the only grouping
    that exists - needs no migration. Any other grouping gets its own `base/<grouping>` subtree.
    """
    if grouping == DEFAULT_GROUPING:
        return base
    return base / grouping
