import asyncio

from prioris_mcp.notes.search_index import SqliteFts5NotesSearchIndex
from prioris_mcp.storage.search_index import SqliteFts5SearchIndex
from prioris_mcp.vector.embedding import FastEmbedBackend
from prioris_mcp.vector.mechanism import FtsMechanism, NotesFtsMechanism, VectorMechanism
from prioris_mcp.vector.sqlite_vec_backend import SqliteVecDocumentBackend


class TestFtsMechanism:
    """Test FtsMechanism's name and status adaptation over SearchIndex."""

    def test_name_is_fts(self, tmp_path):
        mechanism = FtsMechanism(SqliteFts5SearchIndex(tmp_path / "search.sqlite3"))
        assert mechanism.name == "fts"

    def test_status_ready_after_indexing(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        mechanism = FtsMechanism(index)
        asyncio.run(index.index_entries("arxiv", "A", "pdf", [{"key": "k", "start": 0, "length": 1, "text": "x"}]))
        assert asyncio.run(mechanism.status("arxiv", "A", "pdf")) == "ready"

    def test_status_not_built_before_indexing(self, tmp_path):
        mechanism = FtsMechanism(SqliteFts5SearchIndex(tmp_path / "search.sqlite3"))
        assert asyncio.run(mechanism.status("arxiv", "A", "pdf")) == "not_built"


class TestVectorMechanism:
    """Test VectorMechanism's name and search adaptation over DocumentVectorSearchBackend."""

    def test_name_is_vector(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)
        mechanism = VectorMechanism(backend, embedding)
        assert mechanism.name == "vector"

    def test_search_embeds_the_query_text(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)
        mechanism = VectorMechanism(backend, embedding)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "cats"}])
        )
        results = asyncio.run(mechanism.search("cats", provider="arxiv", identifier=None, format=None, limit=10))
        assert results[0]["chunk_id"] == "c1"

    def test_status_delegates_to_backend(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)
        mechanism = VectorMechanism(backend, embedding)
        assert asyncio.run(mechanism.status("arxiv", "A", "pdf")) == "not_built"
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "cats"}])
        )
        assert asyncio.run(mechanism.status("arxiv", "A", "pdf")) == "ready"


class TestNotesFtsMechanism:
    """Test NotesFtsMechanism's name and search adaptation over NotesSearchIndex."""

    def test_name_is_fts(self, tmp_path):
        mechanism = NotesFtsMechanism(SqliteFts5NotesSearchIndex(tmp_path / "notes-search.sqlite3"))
        assert mechanism.name == "fts"

    def test_search_finds_indexed_note_text(self, tmp_path):
        index = SqliteFts5NotesSearchIndex(tmp_path / "notes-search.sqlite3")
        mechanism = NotesFtsMechanism(index)
        asyncio.run(index.index_note("note-1", "a note about ablation studies"))
        results = asyncio.run(mechanism.search("ablation", limit=10))
        assert results == ["note-1"]
