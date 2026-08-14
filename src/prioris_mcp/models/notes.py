"""Pydantic models for NotesBackend and its MCP tool surface.

See docs/requirement-specification/storage/02-notes-storage.md#data-model.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prioris_mcp.models.vector import NoteVectorSearchMatch


class AnchorLocation(BaseModel):
    """Coarse, unvalidated positional hint - never resolved against manifest.sqlite."""

    model_config = ConfigDict(extra="forbid")

    page_number: Annotated[int | None, Field(default=None)] = None
    section_heading: Annotated[str | None, Field(default=None)] = None
    paragraph_index: Annotated[int | None, Field(default=None)] = None

    def is_empty(self) -> bool:
        """Whether every field is unset."""
        return self.page_number is None and self.section_heading is None and self.paragraph_index is None


class AnchorSelectors(BaseModel):
    """W3C Web Annotation-style text-quote selector, adapted (not adopted as a formal dependency)."""

    model_config = ConfigDict(extra="forbid")

    exact_text_quote: Annotated[str | None, Field(default=None)] = None
    prefix_context: Annotated[str | None, Field(default=None)] = None
    suffix_context: Annotated[str | None, Field(default=None)] = None

    def is_empty(self) -> bool:
        """Whether every field is unset."""
        return self.exact_text_quote is None and self.prefix_context is None and self.suffix_context is None


class Anchor(BaseModel):
    """One unresolved, unenforced positional hint on a note.

    See docs/requirement-specification/storage/02-notes-storage.md#anchors-unresolved-positional-hints-not-pointers.
    """

    model_config = ConfigDict(extra="forbid")

    location: Annotated[AnchorLocation | None, Field(default=None)] = None
    selectors: Annotated[AnchorSelectors | None, Field(default=None)] = None

    @model_validator(mode="after")
    def _reject_fully_empty(self) -> "Anchor":
        location_empty = self.location is None or self.location.is_empty()
        selectors_empty = self.selectors is None or self.selectors.is_empty()
        if location_empty and selectors_empty:
            raise ValueError("Anchor requires at least one of location or selectors to carry a value")
        return self


class AuthorFilter(StrEnum):
    """Tri-state author filter for `NotesBackend.search` - a nullable string can't express this.

    See docs/requirement-specification/storage/02-notes-storage.md#search-is-structured-filtering-plus-optional-keyword-matching.
    """

    ANY = "any"
    MINE = "mine"
    NAMED = "named"


class Note(BaseModel):
    """One persisted note - the shape every NotesBackend read/write operation returns."""

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(..., strict=True, description="UUID, also the export filename stem.")]
    provider: Annotated[str, Field(..., strict=True)]
    canonical_identifier: Annotated[str, Field(..., strict=True)]
    format_: Annotated[str | None, Field(default=None, alias="format")] = None
    text: Annotated[str, Field(..., strict=True, description="Free-form Markdown - the note itself.")]
    anchors: Annotated[list[Anchor], Field(default_factory=list)]
    author_name: Annotated[str | None, Field(default=None, description="Null means self.")] = None
    tags: Annotated[list[str], Field(default_factory=list)]
    metadata: Annotated[dict[str, str] | None, Field(default=None, description="Caller-owned, opaque.")] = None
    created_at: Annotated[str, Field(..., strict=True, description="ISO 8601.")]
    updated_at: Annotated[str, Field(..., strict=True, description="ISO 8601.")]


class NoteExport(BaseModel):
    """One note's file representation - the shape the `notes://{id}/export` resource returns."""

    model_config = ConfigDict(extra="forbid")

    suggested_filename: Annotated[str, Field(..., strict=True)]
    frontmatter: Annotated[
        dict,
        Field(
            ...,
            description=(
                "Every Note field except text, as a plain dict - not pre-rendered YAML. The "
                "caller renders this into whatever frontmatter dialect its target tool expects "
                "(e.g. YAML for an Obsidian vault) before writing markdown_body to a file."
            ),
        ),
    ]
    markdown_body: Annotated[str, Field(..., strict=True, description="Exactly the note's own text field.")]


class PagedNotes(BaseModel):
    """Output of `NotesBackend.search` / `research_notes_search`."""

    model_config = ConfigDict(extra="forbid")

    notes: Annotated[list[Note], Field(..., strict=True)]
    offset: Annotated[int, Field(..., strict=True)]
    limit: Annotated[int, Field(..., strict=True)]
    total: Annotated[int, Field(..., strict=True)]
    has_more: Annotated[bool, Field(..., strict=True)]


class NotesSearchResult(BaseModel):
    """Output of `research_notes_search`.

    See docs/requirement-specification/search/02-vector-search.md#composition-a-mode-parameter-not-a-new-opaque-smart-search.
    `fts`/`vector` are each populated only when that mechanism was requested (mode == that name,
    or mode == "hybrid" with a keyword given to embed).

    Unlike documents (naturally scoped by provider+identifier+format), a notes-search request has
    no single-object scope a corpus-wide `index_status` could describe - a structural-filter query
    can span many documents' notes at once. `index_status` is therefore populated only when a
    vector search actually ran this call (mode in ("vector", "hybrid") with a keyword given), as
    the worst-case status across every note actually returned in `vector` this call. There is no
    `fts` key - notes-FTS has no per-request scope to check existence against, unlike documents'.
    """

    model_config = ConfigDict(extra="forbid")

    fts: Annotated[PagedNotes | None, Field(default=None)] = None
    vector: Annotated[list[NoteVectorSearchMatch] | None, Field(default=None)] = None
    index_status: Annotated[dict[str, str] | None, Field(default=None)] = None
