import asyncio

import pytest

from prioris_mcp.models.notes import Anchor, AnchorLocation
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
