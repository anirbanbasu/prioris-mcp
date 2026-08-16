import pytest
from pydantic import ValidationError

from prioris_mcp.models.notes import (
    Anchor,
    AnchorLocation,
    AnchorSelectors,
    AuthorFilter,
    Note,
    NotesSearchResult,
    PagedNotes,
)


class TestAnchor:
    """Tests for Anchor model validation."""

    def test_rejects_entry_with_both_location_and_selectors_absent(self) -> None:
        with pytest.raises(ValidationError, match="location or selectors"):
            Anchor()

    def test_rejects_entry_with_all_null_subfields(self) -> None:
        with pytest.raises(ValidationError, match="location or selectors"):
            Anchor(location=AnchorLocation(), selectors=AnchorSelectors())

    def test_accepts_location_only(self) -> None:
        anchor = Anchor(location=AnchorLocation(page_number=14))
        assert anchor.location is not None
        assert anchor.location.page_number == 14
        assert anchor.selectors is None

    def test_accepts_selectors_only(self) -> None:
        anchor = Anchor(selectors=AnchorSelectors(exact_text_quote="a quote"))
        assert anchor.selectors is not None
        assert anchor.selectors.exact_text_quote == "a quote"


class TestAuthorFilter:
    """Tests for AuthorFilter enum."""

    def test_values(self) -> None:
        assert AuthorFilter.ANY == "any"
        assert AuthorFilter.MINE == "mine"
        assert AuthorFilter.NAMED == "named"


class TestNote:
    """Tests for Note model."""

    def test_format_field_uses_format_alias(self) -> None:
        note = Note.model_validate(
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "provider": "arxiv",
                "canonical_identifier": "2106.09685v2",
                "format": "pdf",
                "text": "a note",
                "anchors": [],
                "author_name": None,
                "tags": [],
                "metadata": None,
                "created_at": "2026-08-08T00:00:00+00:00",
                "updated_at": "2026-08-08T00:00:00+00:00",
            }
        )
        assert note.format_ == "pdf"
        assert note.model_dump(by_alias=True)["format"] == "pdf"

    def test_defaults(self) -> None:
        note = Note(
            id="11111111-1111-1111-1111-111111111111",
            provider="arxiv",
            canonical_identifier="2106.09685v2",
            text="a note",
            created_at="2026-08-08T00:00:00+00:00",
            updated_at="2026-08-08T00:00:00+00:00",
        )
        assert note.format_ is None
        assert note.anchors == []
        assert note.author_name is None
        assert note.tags == []
        assert note.metadata is None


class TestPagedNotes:
    """Tests for PagedNotes model."""

    def test_shape(self) -> None:
        paged = PagedNotes(notes=[], offset=0, limit=50, total=0, has_more=False)
        assert paged.notes == []
        assert paged.has_more is False


class TestNotesSearchResult:
    """Tests for NotesSearchResult model."""

    def test_index_status_defaults_to_none(self) -> None:
        result = NotesSearchResult(fts=None, vector=None)
        assert result.index_status is None

    def test_index_status_accepts_explicit_value(self) -> None:
        result = NotesSearchResult(fts=None, vector=None, index_status={"vector": "ready"})
        assert result.index_status == {"vector": "ready"}
