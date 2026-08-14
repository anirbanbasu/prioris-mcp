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
