"""Pydantic models for VectorSearchBackend results - the MCP wire boundary for vector search.

See docs/requirement-specification/search/02-vector-search.md#result-shape-mirror-ftss-fields-per-corpus-uniform-excerpts-no-score-comparability-requirement.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class VectorSearchMatch(BaseModel):
    """One document-corpus vector search result - mirrors SearchMatch's shape."""

    model_config = ConfigDict(extra="forbid")

    provider: Annotated[str, Field(..., strict=True)]
    identifier: Annotated[str, Field(..., strict=True)]
    format_: Annotated[str, Field(..., alias="format", strict=True)]
    chunk_id: Annotated[str, Field(..., strict=True)]
    offset: Annotated[int, Field(..., strict=True)]
    snippet: Annotated[str, Field(..., strict=True)]
    score: Annotated[float, Field(..., strict=True)]


class PagedVectorSearchMatches(BaseModel):
    """One page of document-corpus vector search results, with paging metadata."""

    model_config = ConfigDict(extra="forbid")

    matches: Annotated[list[VectorSearchMatch], Field(..., strict=True)]
    offset: Annotated[int, Field(..., strict=True)]
    limit: Annotated[int, Field(..., strict=True)]
    total: Annotated[int, Field(..., strict=True)]
    has_more: Annotated[bool, Field(..., strict=True)]


class NoteVectorSearchMatch(BaseModel):
    """One notes-corpus vector search result."""

    model_config = ConfigDict(extra="forbid")

    note_id: Annotated[str, Field(..., strict=True)]
    score: Annotated[float, Field(..., strict=True)]
    text_preview: Annotated[str, Field(..., strict=True)]


class PagedNoteVectorMatches(BaseModel):
    """One page of notes-corpus vector search results, with paging metadata."""

    model_config = ConfigDict(extra="forbid")

    matches: Annotated[list[NoteVectorSearchMatch], Field(..., strict=True)]
    offset: Annotated[int, Field(..., strict=True)]
    limit: Annotated[int, Field(..., strict=True)]
    total: Annotated[int, Field(..., strict=True)]
    has_more: Annotated[bool, Field(..., strict=True)]
