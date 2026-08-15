import pytest
from pydantic import ValidationError

from prioris_mcp.models.vector import (
    NoteVectorSearchMatch,
    PagedNoteVectorMatches,
    PagedVectorSearchMatches,
    VectorSearchMatch,
)


class TestVectorSearchMatch:
    """Construction tests for VectorSearchMatch."""

    def test_construct_with_all_fields(self):
        match = VectorSearchMatch(
            provider="arxiv",
            identifier="2106.09685v2",
            format="pdf",
            chunk_id="c1",
            offset=0,
            snippet="...",
            score=0.12,
        )
        assert match.format_ == "pdf"


class TestNoteVectorSearchMatch:
    """Construction tests for NoteVectorSearchMatch."""

    def test_construct_with_all_fields(self):
        match = NoteVectorSearchMatch(note_id="note-1", score=0.4, text_preview="a preview")
        assert match.note_id == "note-1"


class TestPagedVectorSearchMatches:
    """Construction tests for PagedVectorSearchMatches."""

    def test_construct_with_all_fields(self):
        match = VectorSearchMatch(
            provider="arxiv",
            identifier="2106.09685v2",
            format="pdf",
            chunk_id="c1",
            offset=0,
            snippet="...",
            score=0.12,
        )
        paged = PagedVectorSearchMatches(matches=[match], offset=0, limit=10, total=1, has_more=False)
        assert paged.matches == [match]
        assert paged.offset == 0
        assert paged.limit == 10
        assert paged.total == 1
        assert paged.has_more is False

    def test_rejects_extra_fields(self):
        match = VectorSearchMatch(
            provider="arxiv",
            identifier="2106.09685v2",
            format="pdf",
            chunk_id="c1",
            offset=0,
            snippet="...",
            score=0.12,
        )
        with pytest.raises(ValidationError):
            PagedVectorSearchMatches.model_validate(
                {
                    "matches": [match],
                    "offset": 0,
                    "limit": 10,
                    "total": 1,
                    "has_more": False,
                    "unexpected_field": "value",
                }
            )


class TestPagedNoteVectorMatches:
    """Construction tests for PagedNoteVectorMatches."""

    def test_construct_with_all_fields(self):
        match = NoteVectorSearchMatch(note_id="note-1", score=0.4, text_preview="a preview")
        paged = PagedNoteVectorMatches(matches=[match], offset=0, limit=10, total=1, has_more=False)
        assert paged.matches == [match]
        assert paged.offset == 0
        assert paged.limit == 10
        assert paged.total == 1
        assert paged.has_more is False

    def test_rejects_extra_fields(self):
        match = NoteVectorSearchMatch(note_id="note-1", score=0.4, text_preview="a preview")
        with pytest.raises(ValidationError):
            PagedNoteVectorMatches.model_validate(
                {
                    "matches": [match],
                    "offset": 0,
                    "limit": 10,
                    "total": 1,
                    "has_more": False,
                    "unexpected_field": "value",
                }
            )
