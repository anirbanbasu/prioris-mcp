import asyncio

from prioris_mcp.vector.embedding import FastEmbedBackend
from prioris_mcp.vector.sqlite_vec_backend import SqliteVecNoteBackend


def _backend(tmp_path):
    embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
    return SqliteVecNoteBackend(tmp_path / "notes-vectors.sqlite3", embedding)


class TestIndexAndSearch:
    """Test basic index_note and search functionality."""

    def test_search_empty_index_returns_no_matches(self, tmp_path):
        backend = _backend(tmp_path)
        query = asyncio.run(backend._embedding_backend.embed("anything"))
        assert asyncio.run(backend.search(query)) == []

    def test_indexed_note_is_findable(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.index_note("note-1", "a note about transformer attention mechanisms"))
        query = asyncio.run(backend._embedding_backend.embed("attention-based sequence models"))
        results = asyncio.run(backend.search(query))
        assert results[0]["note_id"] == "note-1"
        assert isinstance(results[0]["score"], float)
        assert "text_preview" in results[0]

    def test_search_scoped_to_note_ids(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.index_note("note-1", "cats"))
        asyncio.run(backend.index_note("note-2", "cats"))
        query = asyncio.run(backend._embedding_backend.embed("cats"))
        results = asyncio.run(backend.search(query, note_ids=["note-1"]))
        assert [r["note_id"] for r in results] == ["note-1"]


class TestReindexAndRemove:
    """Test that reindexing a note replaces its previous vector, and that removal clears it."""

    def test_index_note_replaces_previous_vector(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.index_note("note-1", "aardvark"))
        asyncio.run(backend.index_note("note-1", "zebra"))
        query = asyncio.run(backend._embedding_backend.embed("aardvark"))
        results = asyncio.run(backend.search(query, note_ids=["note-1"]))
        assert results[0]["text_preview"] != "aardvark"

    def test_remove_note_clears_its_vector(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.index_note("note-1", "octopus"))
        asyncio.run(backend.remove_note("note-1"))
        query = asyncio.run(backend._embedding_backend.embed("octopus"))
        assert asyncio.run(backend.search(query, note_ids=["note-1"])) == []

    def test_remove_note_missing_note_is_a_no_op(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.remove_note("nope"))  # must not raise


class TestStatus:
    """Test index_status derivation from recorded-vs-configured embedded_model."""

    def test_status_not_built_before_indexing(self, tmp_path):
        backend = _backend(tmp_path)
        assert asyncio.run(backend.status("note-1")) == "not_built"

    def test_status_ready_after_indexing(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.index_note("note-1", "x"))
        assert asyncio.run(backend.status("note-1")) == "ready"
