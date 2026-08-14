import asyncio

from prioris_mcp.vector.embedding import EmbeddingBackend, FastEmbedBackend
from prioris_mcp.vector.sqlite_vec_backend import SqliteVecDocumentBackend


def _backend(tmp_path):
    embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
    return SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)


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

    async def embed(self, text: str) -> list[float]:
        return [0.1] * self._dim


class TestIndexAndSearch:
    """Test basic index_entries and search functionality."""

    def test_search_empty_index_returns_no_matches(self, tmp_path):
        backend = _backend(tmp_path)
        query = asyncio.run(backend._embedding_backend.embed("anything"))
        assert asyncio.run(backend.search(query)) == []

    def test_indexed_entry_is_findable_by_semantically_similar_query(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries(
                "arxiv",
                "2106.09685v2",
                "pdf",
                [
                    {
                        "chunk_id": "c1",
                        "start": 0,
                        "length": 40,
                        "text": "a transformer architecture for sequence modeling",
                    }
                ],
            )
        )
        query = asyncio.run(backend._embedding_backend.embed("attention-based neural network for sequences"))
        results = asyncio.run(backend.search(query))
        assert len(results) == 1
        assert results[0]["provider"] == "arxiv"
        assert results[0]["chunk_id"] == "c1"
        assert results[0]["offset"] == 0
        assert isinstance(results[0]["score"], float)

    def test_search_scoped_to_provider_filter(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "cats"}])
        )
        asyncio.run(
            backend.index_entries(
                "europepmc", "MED:1", "xml", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "cats"}]
            )
        )
        query = asyncio.run(backend._embedding_backend.embed("cats"))
        results = asyncio.run(backend.search(query, provider="arxiv"))
        assert all(r["provider"] == "arxiv" for r in results)

    def test_search_scoped_to_format_filter(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "cats"}])
        )
        asyncio.run(
            backend.index_entries("arxiv", "A", "xml", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "cats"}])
        )
        query = asyncio.run(backend._embedding_backend.embed("cats"))
        results = asyncio.run(backend.search(query, format="pdf"))
        assert all(r["format"] == "pdf" for r in results)

    def test_unscoped_search_does_not_collapse_same_chunk_id_across_different_documents(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 4, "text": "cats"}])
        )
        asyncio.run(
            backend.index_entries(
                "europepmc", "MED:1", "xml", [{"chunk_id": "c1", "start": 0, "length": 4, "text": "cats"}]
            )
        )
        query = asyncio.run(backend._embedding_backend.embed("cats"))
        results = asyncio.run(backend.search(query))
        document_keys = {(r["provider"], r["identifier"], r["format"]) for r in results}
        assert document_keys == {("arxiv", "A", "pdf"), ("europepmc", "MED:1", "xml")}

    def test_oversized_chunk_sub_splits_collapse_to_one_result_per_chunk_id(self, tmp_path):
        backend = _backend(tmp_path)
        long_text = "the transformer architecture uses self attention. " * 100
        asyncio.run(
            backend.index_entries(
                "arxiv",
                "2106.09685v2",
                "pdf",
                [{"chunk_id": "c1", "start": 0, "length": len(long_text), "text": long_text}],
            )
        )
        query = asyncio.run(backend._embedding_backend.embed("self attention mechanism"))
        results = asyncio.run(backend.search(query))
        chunk_ids = [r["chunk_id"] for r in results]
        assert chunk_ids.count("c1") == 1


class TestReplaceOnReindex:
    """Test that indexing the same document replaces previous vectors."""

    def test_index_entries_replaces_previous_vectors_for_same_document(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries(
                "arxiv", "A", "pdf", [{"chunk_id": "old", "start": 0, "length": 5, "text": "aardvark"}]
            )
        )
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "new", "start": 0, "length": 5, "text": "zebra"}])
        )
        query = asyncio.run(backend._embedding_backend.embed("aardvark"))
        results = asyncio.run(backend.search(query, provider="arxiv", identifier="A"))
        assert all(r["chunk_id"] != "old" for r in results)


class TestRemoveDocument:
    """Test remove_document functionality."""

    def test_remove_document_clears_its_vectors(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "octopus"}])
        )
        asyncio.run(backend.remove_document("arxiv", "A", "pdf"))
        query = asyncio.run(backend._embedding_backend.embed("octopus"))
        results = asyncio.run(backend.search(query, provider="arxiv", identifier="A"))
        assert results == []

    def test_remove_document_missing_document_is_a_no_op(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(backend.remove_document("arxiv", "nope", "pdf"))  # must not raise


class TestStatus:
    """Test index_status derivation from recorded-vs-configured embedded_model."""

    def test_status_not_built_before_indexing(self, tmp_path):
        backend = _backend(tmp_path)
        assert asyncio.run(backend.status("arxiv", "A", "pdf")) == "not_built"

    def test_status_ready_after_indexing_with_current_model(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "x"}])
        )
        assert asyncio.run(backend.status("arxiv", "A", "pdf")) == "ready"

    def test_status_stale_when_recorded_model_differs_from_configured(self, tmp_path):
        embedding = FastEmbedBackend("BAAI/bge-small-en-v1.5")
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "x"}])
        )
        # Simulate a model-name change by pointing a second backend instance, same file, at a
        # differently-named (but same-dimension) EmbeddingBackend stub.
        from prioris_mcp.vector.embedding import EmbeddingBackend

        class _RenamedStub(EmbeddingBackend):
            model_name = "a-different-model"
            dimension = embedding.dimension

            async def embed(self, text):
                return await embedding.embed(text)

        stale_view = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", _RenamedStub())
        assert asyncio.run(stale_view.status("arxiv", "A", "pdf")) == "stale"

    def test_reindexing_under_a_different_dimension_model_self_heals(self, tmp_path):
        db_path = tmp_path / "vectors.sqlite3"
        backend_a = SqliteVecDocumentBackend(db_path, _StubEmbedding("model-a", 4))
        asyncio.run(
            backend_a.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "old"}])
        )

        backend_b = SqliteVecDocumentBackend(db_path, _StubEmbedding("model-b", 8))
        assert asyncio.run(backend_b.status("arxiv", "A", "pdf")) == "stale"

        # Must not raise sqlite3.OperationalError despite the old vec0 table being built at
        # dimension 4 while backend_b embeds at dimension 8.
        asyncio.run(
            backend_b.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c2", "start": 0, "length": 5, "text": "new"}])
        )
        assert asyncio.run(backend_b.status("arxiv", "A", "pdf")) == "ready"

        query = asyncio.run(backend_b._embedding_backend.embed("new"))
        results = asyncio.run(backend_b.search(query, provider="arxiv", identifier="A"))
        assert [r["chunk_id"] for r in results] == ["c2"]
