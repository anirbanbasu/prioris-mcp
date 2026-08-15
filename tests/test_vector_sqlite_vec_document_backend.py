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

    @property
    def max_chunk_chars(self) -> int:
        return 2000

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


class TestOffsetPaging:
    """Test search() offset paging."""

    def test_search_offset_paginates_without_overlap_or_gaps(self, tmp_path):
        backend = _backend(tmp_path)
        words = ["apple", "banana", "cherry", "date", "elderberry"]
        for i, word in enumerate(words):
            asyncio.run(
                backend.index_entries(
                    "arxiv", f"doc-{i}", "pdf", [{"chunk_id": "c1", "start": 0, "length": len(word), "text": word}]
                )
            )
        query = asyncio.run(backend._embedding_backend.embed("fruit"))
        full = asyncio.run(backend.search(query, limit=5))
        assert len(full) == 5
        page1 = asyncio.run(backend.search(query, offset=0, limit=2))
        page2 = asyncio.run(backend.search(query, offset=2, limit=2))
        page3 = asyncio.run(backend.search(query, offset=4, limit=2))
        assert page1 == full[0:2]
        assert page2 == full[2:4]
        assert page3 == full[4:5]

    def test_search_offset_beyond_available_results_returns_empty(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 4, "text": "cats"}])
        )
        query = asyncio.run(backend._embedding_backend.embed("cats"))
        assert asyncio.run(backend.search(query, offset=10, limit=5)) == []

    def test_search_offset_paginates_correctly_when_winning_sub_splits_exceed_the_overfetch_window(self, tmp_path):
        """Regression test for a parent chunk with more winning sub-splits than the initial KNN window.

        That used to starve `best_per_chunk`, making a later page report `[]` even though
        `count()` said distinct chunks remained. See `sqlite_vec_backend.py`'s `search()` loop.
        """

        class _CoordEmbedding(EmbeddingBackend):
            """Stub returning a fixed 2-D vector per exact input text, for deterministic KNN ranking."""

            model_name = "coord-stub"
            dimension = 2
            max_chunk_chars = 2000

            def __init__(self, vectors: dict[str, list[float]]) -> None:
                self._vectors = vectors

            async def embed(self, text: str) -> list[float]:
                return self._vectors[text]

        # 100 sub-splits of one oversized "winner" chunk, all identical text so they all embed to
        # the same (closest-to-query) vector - more than `_CHUNK_COLLAPSE_OVERFETCH_FACTOR *
        # needed` (4 * 10 = 40) for the first page below. 10 single-window "loser" chunks, each
        # with its own distinct chunk_id and a monotonically farther-from-query vector.
        vectors = {"a": [1.0, 0.0]}
        for i in range(10):
            vectors[str(i)] = [1 - (i + 1) * 0.05, (i + 1) * 0.05]
        embedding = _CoordEmbedding(vectors)
        backend = SqliteVecDocumentBackend(tmp_path / "vectors.sqlite3", embedding, max_chunk_chars=1)

        entries = [{"chunk_id": "winner", "start": 0, "length": 100, "text": "a" * 100}]
        entries += [{"chunk_id": f"loser{i}", "start": 0, "length": 1, "text": str(i)} for i in range(10)]
        asyncio.run(backend.index_entries("arxiv", "doc", "pdf", entries))

        assert asyncio.run(backend.count()) == 11

        query = [1.0, 0.0]
        page1 = asyncio.run(backend.search(query, offset=0, limit=10))
        page2 = asyncio.run(backend.search(query, offset=10, limit=10))
        assert len(page1) == 10
        # Previously: the winner's 100 tied-distance sub-splits alone filled the fixed overfetch
        # window, so only the winner chunk was ever discovered and this page came back empty even
        # though an 11th distinct chunk (loser9) exists.
        assert len(page2) == 1
        assert {r["chunk_id"] for r in page1} | {r["chunk_id"] for r in page2} == {"winner"} | {
            f"loser{i}" for i in range(10)
        }

    def test_search_offset_still_respects_chunk_id_collapse_for_oversized_chunks(self, tmp_path):
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
        asyncio.run(
            backend.index_entries(
                "arxiv", "other", "pdf", [{"chunk_id": "c2", "start": 0, "length": 4, "text": "cats"}]
            )
        )
        query = asyncio.run(backend._embedding_backend.embed("self attention mechanism"))
        page1 = asyncio.run(backend.search(query, provider="arxiv", format="pdf", offset=0, limit=1))
        page2 = asyncio.run(backend.search(query, provider="arxiv", format="pdf", offset=1, limit=1))
        assert len(page1) == 1
        assert len(page2) == 1
        assert page1[0]["chunk_id"] != page2[0]["chunk_id"]


class TestCount:
    """Test count() — distinct-chunk counting, unpaged."""

    def test_count_zero_for_empty_index(self, tmp_path):
        backend = _backend(tmp_path)
        assert asyncio.run(backend.count()) == 0

    def test_count_matches_number_of_indexed_chunks(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 4, "text": "cats"}])
        )
        asyncio.run(
            backend.index_entries(
                "europepmc", "MED:1", "xml", [{"chunk_id": "c1", "start": 0, "length": 4, "text": "cats"}]
            )
        )
        assert asyncio.run(backend.count()) == 2

    def test_count_scoped_by_provider_identifier_format(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 4, "text": "cats"}])
        )
        asyncio.run(
            backend.index_entries(
                "europepmc", "MED:1", "xml", [{"chunk_id": "c1", "start": 0, "length": 4, "text": "cats"}]
            )
        )
        assert asyncio.run(backend.count(provider="arxiv")) == 1
        assert asyncio.run(backend.count(identifier="MED:1")) == 1
        assert asyncio.run(backend.count(format="xml")) == 1

    def test_count_reflects_removal(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 4, "text": "cats"}])
        )
        assert asyncio.run(backend.count()) == 1
        asyncio.run(backend.remove_document("arxiv", "A", "pdf"))
        assert asyncio.run(backend.count()) == 0

    def test_count_counts_oversized_chunk_once_matching_what_search_can_return(self, tmp_path):
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
        asyncio.run(
            backend.index_entries(
                "arxiv", "other", "pdf", [{"chunk_id": "c2", "start": 0, "length": 4, "text": "cats"}]
            )
        )
        count = asyncio.run(backend.count(provider="arxiv", format="pdf"))
        assert count == 2

        query = asyncio.run(backend._embedding_backend.embed("self attention mechanism"))
        results = asyncio.run(backend.search(query, provider="arxiv", format="pdf", limit=count))
        assert len(results) == count
        # One more page beyond `count` must be empty - there is nothing left to collapse into.
        overflow = asyncio.run(backend.search(query, provider="arxiv", format="pdf", offset=count, limit=5))
        assert overflow == []


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
            max_chunk_chars = embedding.max_chunk_chars

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

    def test_reindexing_with_empty_entries_reverts_status_to_not_built(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 5, "text": "x"}])
        )
        assert asyncio.run(backend.status("arxiv", "A", "pdf")) == "ready"

        asyncio.run(backend.index_entries("arxiv", "A", "pdf", []))
        assert asyncio.run(backend.status("arxiv", "A", "pdf")) == "not_built"

        query = asyncio.run(backend._embedding_backend.embed("x"))
        assert asyncio.run(backend.search(query, provider="arxiv", identifier="A")) == []

    def test_status_stale_for_a_same_dimension_differently_named_model(self, tmp_path):
        """The Low finding this task fixes: same dimension, different model name, must still be stale."""
        db_path = tmp_path / "vectors.sqlite3"
        backend_a = SqliteVecDocumentBackend(db_path, _StubEmbedding("model-a", 4))
        asyncio.run(
            backend_a.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 3, "text": "old"}])
        )

        backend_b = SqliteVecDocumentBackend(db_path, _StubEmbedding("model-b", 4))  # same dimension
        assert asyncio.run(backend_b.status("arxiv", "A", "pdf")) == "stale"

        # Must not raise despite backend_b's table already existing under model-a's rows - a
        # same-dimension mismatch must still trigger the drop-and-recreate, not silently reuse
        # model-a's stale vectors.
        asyncio.run(
            backend_b.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c2", "start": 0, "length": 3, "text": "new"}])
        )
        assert asyncio.run(backend_b.status("arxiv", "A", "pdf")) == "ready"
        query = asyncio.run(backend_b._embedding_backend.embed("new"))
        results = asyncio.run(backend_b.search(query, provider="arxiv", identifier="A"))
        assert [r["chunk_id"] for r in results] == ["c2"]


class TestIndexedUnder:
    """Test indexed_under() - the corpus-diffing primitive reconciliation uses (Task 1)."""

    def test_empty_index_returns_empty_set(self, tmp_path):
        backend = _backend(tmp_path)
        assert asyncio.run(backend.indexed_under(backend._embedding_backend.model_name)) == set()

    def test_returns_every_document_indexed_under_that_model(self, tmp_path):
        backend = _backend(tmp_path)
        asyncio.run(
            backend.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 3, "text": "cat"}])
        )
        asyncio.run(
            backend.index_entries(
                "europepmc", "MED:1", "xml", [{"chunk_id": "c1", "start": 0, "length": 3, "text": "dog"}]
            )
        )
        result = asyncio.run(backend.indexed_under(backend._embedding_backend.model_name))
        assert result == {("arxiv", "A", "pdf"), ("europepmc", "MED:1", "xml")}

    def test_excludes_documents_indexed_under_a_different_model(self, tmp_path):
        db_path = tmp_path / "vectors.sqlite3"
        backend_a = SqliteVecDocumentBackend(db_path, _StubEmbedding("model-a", 4))
        asyncio.run(
            backend_a.index_entries("arxiv", "A", "pdf", [{"chunk_id": "c1", "start": 0, "length": 3, "text": "cat"}])
        )
        assert asyncio.run(backend_a.indexed_under("model-b")) == set()
        assert asyncio.run(backend_a.indexed_under("model-a")) == {("arxiv", "A", "pdf")}
