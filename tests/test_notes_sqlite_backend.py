import asyncio

import pytest

from prioris_mcp.models.notes import Anchor, AnchorLocation, AuthorFilter
from prioris_mcp.notes.sqlite_backend import SqliteNotesBackend


class TestSqliteNotesBackendCreate:
    """Test SqliteNotesBackend.create method."""

    def test_create_generates_id_and_timestamps(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        note = asyncio.run(backend.create("arxiv", "2106.09685v2", "pdf", "a note about the ablation study"))
        assert note.id
        assert note.created_at
        assert note.updated_at == note.created_at
        assert note.provider == "arxiv"
        assert note.canonical_identifier == "2106.09685v2"
        assert note.format_ == "pdf"
        assert note.text == "a note about the ablation study"

    def test_create_defaults(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        note = asyncio.run(backend.create("arxiv", "2106.09685v2", None, "a note"))
        assert note.format_ is None
        assert note.anchors == []
        assert note.author_name is None
        assert note.tags == []
        assert note.metadata is None

    def test_create_round_trips_anchors_tags_metadata(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        anchor = Anchor(location=AnchorLocation(page_number=14))
        note = asyncio.run(
            backend.create(
                "arxiv",
                "2106.09685v2",
                "pdf",
                "a note",
                anchors=[anchor],
                author_name="Dr. Advisor",
                tags=["methodology", "follow-up"],
                metadata={"quiz_score": "8/10"},
            )
        )
        assert note.anchors == [anchor]
        assert note.author_name == "Dr. Advisor"
        assert note.tags == ["methodology", "follow-up"]
        assert note.metadata == {"quiz_score": "8/10"}

    def test_create_rejects_fully_empty_anchor(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        with pytest.raises(ValueError, match="location or selectors"):
            asyncio.run(backend.create("arxiv", "2106.09685v2", "pdf", "a note", anchors=[Anchor()]))


class TestSqliteNotesBackendRead:
    """Test SqliteNotesBackend.read method."""

    def test_read_missing_note_raises_file_not_found(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        with pytest.raises(FileNotFoundError):
            asyncio.run(backend.read("does-not-exist"))

    def test_read_returns_created_note(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        created = asyncio.run(backend.create("arxiv", "2106.09685v2", "pdf", "a note"))
        read_back = asyncio.run(backend.read(created.id))
        assert read_back == created


class TestSqliteNotesBackendUpdate:
    """Test SqliteNotesBackend.update method."""

    def test_update_missing_note_raises_file_not_found(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        with pytest.raises(FileNotFoundError):
            asyncio.run(backend.update("does-not-exist", text="new text"))

    def test_update_text_bumps_updated_at_but_not_created_at(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        created = asyncio.run(backend.create("arxiv", "2106.09685v2", "pdf", "original text"))
        updated = asyncio.run(backend.update(created.id, text="revised text"))
        assert updated.text == "revised text"
        assert updated.created_at == created.created_at
        assert updated.updated_at != created.created_at

    def test_update_only_touches_given_fields(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        created = asyncio.run(backend.create("arxiv", "2106.09685v2", "pdf", "text", tags=["a"]))
        updated = asyncio.run(backend.update(created.id, tags=["a", "b"]))
        assert updated.text == "text"
        assert updated.tags == ["a", "b"]

    def test_update_can_clear_anchors_and_metadata_with_empty_values(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        created = asyncio.run(backend.create("arxiv", "2106.09685v2", "pdf", "text", metadata={"k": "v"}))
        updated = asyncio.run(backend.update(created.id, anchors=[], metadata={}))
        assert updated.anchors == []
        assert updated.metadata == {}


class TestSqliteNotesBackendDelete:
    """Test SqliteNotesBackend.delete method."""

    def test_delete_missing_note_returns_false(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        assert asyncio.run(backend.delete("does-not-exist")) is False

    def test_delete_removes_note(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        created = asyncio.run(backend.create("arxiv", "2106.09685v2", "pdf", "text"))
        assert asyncio.run(backend.delete(created.id)) is True
        with pytest.raises(FileNotFoundError):
            asyncio.run(backend.read(created.id))


class TestSqliteNotesBackendSearchStructuredFilters:
    """Test SqliteNotesBackend.search method with structured filters."""

    def _seed(self, backend):
        asyncio.run(backend.create("arxiv", "A", "pdf", "note A", author_name=None, tags=["x", "y"]))
        asyncio.run(backend.create("arxiv", "B", "pdf", "note B", author_name="Dr. Advisor", tags=["x"]))
        asyncio.run(backend.create("europepmc", "C", "pdf", "note C", author_name=None, tags=["y", "z"]))

    def test_no_filters_returns_everything_newest_first(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        result = asyncio.run(backend.search())
        assert [n.canonical_identifier for n in result.notes] == ["C", "B", "A"]
        assert result.total == 3
        assert result.has_more is False

    def test_provider_filter(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        result = asyncio.run(backend.search(provider="europepmc"))
        assert [n.canonical_identifier for n in result.notes] == ["C"]

    def test_provider_and_canonical_identifier_filter(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        result = asyncio.run(backend.search(provider="arxiv", canonical_identifier="B"))
        assert [n.canonical_identifier for n in result.notes] == ["B"]

    def test_canonical_identifier_without_provider_raises(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        with pytest.raises(ValueError, match="provider"):
            asyncio.run(backend.search(canonical_identifier="B"))

    def test_pagination(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        page = asyncio.run(backend.search(offset=0, limit=2))
        assert len(page.notes) == 2
        assert page.total == 3
        assert page.has_more is True
        next_page = asyncio.run(backend.search(offset=2, limit=2))
        assert len(next_page.notes) == 1
        assert next_page.has_more is False

    def test_author_filter_any_is_default_and_returns_all(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        result = asyncio.run(backend.search())
        assert len(result.notes) == 3

    def test_author_filter_mine_returns_only_null_author(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        result = asyncio.run(backend.search(author_filter=AuthorFilter.MINE))
        assert {n.canonical_identifier for n in result.notes} == {"A", "C"}

    def test_author_filter_named_returns_only_that_author(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        result = asyncio.run(backend.search(author_filter=AuthorFilter.NAMED, author_name="Dr. Advisor"))
        assert {n.canonical_identifier for n in result.notes} == {"B"}

    def test_author_filter_named_without_author_name_raises(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        with pytest.raises(ValueError, match="author_name"):
            asyncio.run(backend.search(author_filter=AuthorFilter.NAMED))

    def test_author_name_without_named_filter_raises(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        with pytest.raises(ValueError, match="author_name"):
            asyncio.run(backend.search(author_filter=AuthorFilter.ANY, author_name="Dr. Advisor"))

    def test_tags_all_requires_every_tag(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        result = asyncio.run(backend.search(tags_all=["x", "y"]))
        assert {n.canonical_identifier for n in result.notes} == {"A"}

    def test_tags_any_requires_at_least_one_tag(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        result = asyncio.run(backend.search(tags_any=["z"]))
        assert {n.canonical_identifier for n in result.notes} == {"C"}

    def test_tags_exclude_removes_matching_notes(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        result = asyncio.run(backend.search(tags_exclude=["x"]))
        assert {n.canonical_identifier for n in result.notes} == {"C"}

    def test_format_filter(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        asyncio.run(backend.create("arxiv", "D", "epub", "note D", author_name=None, tags=[]))
        asyncio.run(backend.create("arxiv", "E", "pdf", "note E", author_name=None, tags=[]))
        result = asyncio.run(backend.search(format="epub"))
        assert {n.canonical_identifier for n in result.notes} == {"D"}

    def test_date_range_filters_by_created_at(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        far_future = "2999-01-01T00:00:00+00:00"
        result = asyncio.run(backend.search(date_from=far_future))
        assert result.notes == []

    def test_date_to_filter(self, tmp_path):
        backend = SqliteNotesBackend(tmp_path / "notes.sqlite")
        self._seed(backend)
        far_past = "1970-01-01T00:00:00+00:00"
        result = asyncio.run(backend.search(date_to=far_past))
        assert result.notes == []
