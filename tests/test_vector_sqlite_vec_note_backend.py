import asyncio

from prioris_mcp.vector.embedding import EmbeddingBackend, FastEmbedBackend
from prioris_mcp.vector.sqlite_vec_backend import SqliteVecNoteBackend


def _backend(tmp_path):
    embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
    return SqliteVecNoteBackend(tmp_path / "notes-vectors.sqlite3", embedding)


class _StubEmbedding(EmbeddingBackend):
    """Lightweight fake EmbeddingBackend with a caller-controlled fixed dimension.

    Avoids spinning up two real fastembed models (slow/network-dependent) just to exercise a
    dimension change.
    """

    def __init__(self, model_name: str, dimension: int) -> None:
        self._model_name = model_name
        self._dim = dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def max_chunk_chars(self) -> int:
        return 2000

    async def embed(self, text: str) -> list[float]:
        return [0.1] * self._dim


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

    def test_status_stale_when_recorded_model_differs_from_configured(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecNoteBackend(tmp_path / "notes-vectors.sqlite3", embedding)
        asyncio.run(backend.index_note("note-1", "x"))

        # Simulate a model-name change by pointing a second backend instance, same file, at a
        # differently-named (but same-dimension) EmbeddingBackend stub.
        class _RenamedStub(EmbeddingBackend):
            model_name = "a-different-model"
            dimension = embedding.dimension
            max_chunk_chars = embedding.max_chunk_chars

            async def embed(self, text):
                return await embedding.embed(text)

        stale_view = SqliteVecNoteBackend(tmp_path / "notes-vectors.sqlite3", _RenamedStub())
        assert asyncio.run(stale_view.status("note-1")) == "stale"

    def test_reindexing_under_a_different_dimension_model_self_heals(self, tmp_path):
        db_path = tmp_path / "notes-vectors.sqlite3"
        backend_a = SqliteVecNoteBackend(db_path, _StubEmbedding("model-a", 4))
        asyncio.run(backend_a.index_note("note-1", "old"))

        backend_b = SqliteVecNoteBackend(db_path, _StubEmbedding("model-b", 8))
        assert asyncio.run(backend_b.status("note-1")) == "stale"

        # Must not raise sqlite3.OperationalError despite the old vec0 table being built at
        # dimension 4 while backend_b embeds at dimension 8.
        asyncio.run(backend_b.index_note("note-1", "new"))
        assert asyncio.run(backend_b.status("note-1")) == "ready"

        query = asyncio.run(backend_b._embedding_backend.embed("new"))
        results = asyncio.run(backend_b.search(query, note_ids=["note-1"]))
        assert [r["note_id"] for r in results] == ["note-1"]


class TestHasAnyIndexed:
    """Test the corpus-wide has_any_indexed existence check (D6)."""

    def test_false_on_a_never_indexed_corpus(self, tmp_path):
        backend = _backend(tmp_path)
        assert asyncio.run(backend.has_any_indexed(backend._embedding_backend.model_name)) is False

    def test_true_once_at_least_one_note_is_indexed_under_that_model(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.index_note("note-1", "x"))
        assert asyncio.run(backend.has_any_indexed(backend._embedding_backend.model_name)) is True

    def test_false_for_a_different_model_name_than_what_was_indexed(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.index_note("note-1", "x"))
        assert asyncio.run(backend.has_any_indexed("some-other-model")) is False


class TestPagination:
    """Test offset/limit pagination for search."""

    def test_offset_skips_results(self, tmp_path):
        backend = _backend(tmp_path)
        # Index multiple notes with identical semantic distance (same text).
        asyncio.run(backend.index_note("note-1", "the same text"))
        asyncio.run(backend.index_note("note-2", "the same text"))
        asyncio.run(backend.index_note("note-3", "the same text"))

        query = asyncio.run(backend._embedding_backend.embed("the same text"))

        # Get first page (offset=0, limit=2).
        page1 = asyncio.run(backend.search(query, offset=0, limit=2))
        assert len(page1) == 2

        # Get second page (offset=2, limit=2).
        page2 = asyncio.run(backend.search(query, offset=2, limit=2))
        assert len(page2) == 1

        # Ensure pages don't overlap.
        page1_ids = {r["note_id"] for r in page1}
        page2_ids = {r["note_id"] for r in page2}
        assert page1_ids.isdisjoint(page2_ids)

        # Ensure all notes are covered across pages.
        all_ids = page1_ids | page2_ids
        assert all_ids == {"note-1", "note-2", "note-3"}

    def test_offset_with_note_ids_scoping(self, tmp_path):
        backend = _backend(tmp_path)
        # Index notes with same text.
        asyncio.run(backend.index_note("note-a", "shared text"))
        asyncio.run(backend.index_note("note-b", "shared text"))
        asyncio.run(backend.index_note("note-c", "shared text"))

        query = asyncio.run(backend._embedding_backend.embed("shared text"))

        # Scope to a subset, then paginate.
        page1 = asyncio.run(backend.search(query, note_ids=["note-a", "note-b"], offset=0, limit=1))
        assert len(page1) == 1

        page2 = asyncio.run(backend.search(query, note_ids=["note-a", "note-b"], offset=1, limit=1))
        assert len(page2) == 1

        # Scoped search shouldn't return note-c.
        all_ids = {r["note_id"] for r in page1 + page2}
        assert all_ids <= {"note-a", "note-b"}


class TestCount:
    """Test count() method for corpus-wide and scoped counts."""

    def test_count_empty_index_is_zero(self, tmp_path):
        backend = _backend(tmp_path)
        assert asyncio.run(backend.count()) == 0

    def test_count_increases_with_indexed_notes(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.index_note("note-1", "first"))
        assert asyncio.run(backend.count()) == 1

        asyncio.run(backend.index_note("note-2", "second"))
        assert asyncio.run(backend.count()) == 2

    def test_count_scoped_to_note_ids(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.index_note("note-1", "a"))
        asyncio.run(backend.index_note("note-2", "b"))
        asyncio.run(backend.index_note("note-3", "c"))

        # Count all notes.
        assert asyncio.run(backend.count()) == 3

        # Count only a subset.
        assert asyncio.run(backend.count(note_ids=["note-1", "note-2"])) == 2

        # Count single note.
        assert asyncio.run(backend.count(note_ids=["note-1"])) == 1

    def test_count_reflects_removals(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.index_note("note-1", "x"))
        asyncio.run(backend.index_note("note-2", "y"))
        assert asyncio.run(backend.count()) == 2

        asyncio.run(backend.remove_note("note-1"))
        assert asyncio.run(backend.count()) == 1

    def test_count_scoped_unrelated_note_ids_is_zero(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.index_note("note-1", "x"))
        # Count with note_ids that don't match anything.
        assert asyncio.run(backend.count(note_ids=["note-999", "note-1000"])) == 0
