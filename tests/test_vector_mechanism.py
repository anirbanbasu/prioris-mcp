import asyncio

from prioris_mcp.notes.search_index import SqliteFts5NotesSearchIndex
from prioris_mcp.storage.search_index import SqliteFts5SearchIndex
from prioris_mcp.vector.embedding import FastEmbedBackend
from prioris_mcp.vector.mechanism import FtsMechanism, NotesFtsMechanism, NotesVectorMechanism, VectorMechanism
from prioris_mcp.vector.scheduler import EmbeddingScheduler
from prioris_mcp.vector.sqlite_vec_backend import SqliteVecDocumentBackend, SqliteVecNoteBackend


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

    def test_search_respects_offset(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        mechanism = FtsMechanism(index)
        asyncio.run(
            index.index_entries(
                "arxiv",
                "A",
                "pdf",
                [
                    {"key": "k1", "start": 0, "length": 1, "text": "cats cats cats"},
                    {"key": "k2", "start": 1, "length": 1, "text": "cats cats"},
                    {"key": "k3", "start": 2, "length": 1, "text": "cats"},
                ],
            )
        )
        full = asyncio.run(mechanism.search("cats", provider=None, identifier=None, format=None, limit=10))
        assert len(full) == 3
        paged = asyncio.run(mechanism.search("cats", provider=None, identifier=None, format=None, offset=1, limit=1))
        assert paged == full[1:2]

    def test_count_ignores_offset_and_limit(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        mechanism = FtsMechanism(index)
        asyncio.run(
            index.index_entries(
                "arxiv",
                "A",
                "pdf",
                [
                    {"key": "k1", "start": 0, "length": 1, "text": "cats"},
                    {"key": "k2", "start": 1, "length": 1, "text": "cats"},
                    {"key": "k3", "start": 2, "length": 1, "text": "cats"},
                ],
            )
        )
        assert asyncio.run(mechanism.count("cats", provider=None, identifier=None, format=None)) == 3


class TestVectorMechanism:
    """Test VectorMechanism's name and search adaptation over DocumentVectorSearchBackend."""

    def test_name_is_vector(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)
        mechanism = VectorMechanism(backend, embedding, EmbeddingScheduler())
        assert mechanism.name == "vector"

    def test_search_embeds_the_query_text(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)
        mechanism = VectorMechanism(backend, embedding, EmbeddingScheduler())
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "cats"}])
        )
        results = asyncio.run(mechanism.search("cats", provider="arxiv", identifier=None, format=None, limit=10))
        assert results[0]["chunk_id"] == "c1"

    def test_status_delegates_to_backend(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)
        mechanism = VectorMechanism(backend, embedding, EmbeddingScheduler())
        assert asyncio.run(mechanism.status("arxiv", "A", "pdf")) == "not_built"
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "cats"}])
        )
        assert asyncio.run(mechanism.status("arxiv", "A", "pdf")) == "ready"

    def test_status_reports_building_while_a_scheduler_task_is_in_flight(self, tmp_path):
        """`status` must consult the scheduler, not only the backend's persisted state.

        Otherwise a poll immediately after scheduling a (re-)embed sees whatever the backend's
        last completed write left behind (`not_built`/`stale`/even a stale `ready`), not
        `building` - see docs/requirement-specification/search/02-vector-search.md#index-status-is-per-documentnote-derived-by-comparing-recorded-vs-configured-model.
        """
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)
        scheduler = EmbeddingScheduler()
        mechanism = VectorMechanism(backend, embedding, scheduler)

        gate = asyncio.Event()

        async def scenario():
            scheduler.schedule(("arxiv", "A", "pdf"), gate.wait)
            during = await mechanism.status("arxiv", "A", "pdf")
            gate.set()
            await scheduler.wait_all()
            after = await mechanism.status("arxiv", "A", "pdf")
            return during, after

        during, after = asyncio.run(scenario())
        assert during == "building"
        assert after == "not_built"

    def test_search_respects_offset(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)
        mechanism = VectorMechanism(backend, embedding, EmbeddingScheduler())
        asyncio.run(
            backend.index_entries(
                "arxiv",
                "A",
                "pdf",
                [
                    {"chunk_id": "c1", "start": 0, "length": 5, "text": "cats"},
                    {"chunk_id": "c2", "start": 5, "length": 5, "text": "cats"},
                    {"chunk_id": "c3", "start": 10, "length": 5, "text": "cats"},
                ],
            )
        )
        full = asyncio.run(mechanism.search("cats", provider="arxiv", identifier=None, format=None, limit=10))
        assert len(full) == 3
        paged = asyncio.run(mechanism.search("cats", provider="arxiv", identifier=None, format=None, offset=1, limit=1))
        assert paged == full[1:2]

    def test_count_ignores_offset_and_limit(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)
        mechanism = VectorMechanism(backend, embedding, EmbeddingScheduler())
        asyncio.run(
            backend.index_entries(
                "arxiv",
                "A",
                "pdf",
                [
                    {"chunk_id": "c1", "start": 0, "length": 5, "text": "cats"},
                    {"chunk_id": "c2", "start": 5, "length": 5, "text": "cats"},
                    {"chunk_id": "c3", "start": 10, "length": 5, "text": "cats"},
                ],
            )
        )
        count = asyncio.run(mechanism.count("cats", provider="arxiv", identifier=None, format=None))
        assert count == 3


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


class TestNotesVectorMechanism:
    """Test NotesVectorMechanism's name and search adaptation over NoteVectorSearchBackend."""

    def test_name_is_vector(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecNoteBackend(tmp_path / "notes-vectors.sqlite3", embedding)
        mechanism = NotesVectorMechanism(backend, embedding)
        assert mechanism.name == "vector"

    def test_search_embeds_the_query_text(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecNoteBackend(tmp_path / "notes-vectors.sqlite3", embedding)
        mechanism = NotesVectorMechanism(backend, embedding)
        asyncio.run(backend.index_note("note-1", "cats"))
        results = asyncio.run(mechanism.search("cats", limit=10))
        assert results[0]["note_id"] == "note-1"

    def test_search_respects_note_ids_scoping(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecNoteBackend(tmp_path / "notes-vectors.sqlite3", embedding)
        mechanism = NotesVectorMechanism(backend, embedding)
        asyncio.run(backend.index_note("note-1", "cats"))
        asyncio.run(backend.index_note("note-2", "cats"))
        results = asyncio.run(mechanism.search("cats", note_ids=["note-1"], limit=10))
        assert [r["note_id"] for r in results] == ["note-1"]

    def test_search_respects_offset(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecNoteBackend(tmp_path / "notes-vectors.sqlite3", embedding)
        mechanism = NotesVectorMechanism(backend, embedding)
        asyncio.run(backend.index_note("note-1", "cats"))
        asyncio.run(backend.index_note("note-2", "cats"))
        asyncio.run(backend.index_note("note-3", "cats"))
        full = asyncio.run(mechanism.search("cats", limit=10))
        assert len(full) == 3
        paged = asyncio.run(mechanism.search("cats", offset=1, limit=1))
        assert paged == full[1:2]

    def test_count_ignores_offset_and_limit(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecNoteBackend(tmp_path / "notes-vectors.sqlite3", embedding)
        mechanism = NotesVectorMechanism(backend, embedding)
        asyncio.run(backend.index_note("note-1", "cats"))
        asyncio.run(backend.index_note("note-2", "cats"))
        asyncio.run(backend.index_note("note-3", "cats"))
        assert asyncio.run(mechanism.count()) == 3

    def test_count_respects_note_ids_scoping(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecNoteBackend(tmp_path / "notes-vectors.sqlite3", embedding)
        mechanism = NotesVectorMechanism(backend, embedding)
        asyncio.run(backend.index_note("note-1", "cats"))
        asyncio.run(backend.index_note("note-2", "cats"))
        assert asyncio.run(mechanism.count(note_ids=["note-1"])) == 1
