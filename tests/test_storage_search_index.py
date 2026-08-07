import asyncio

from prioris_mcp.storage.search_index import SqliteFts5SearchIndex


class TestIndexEntriesAndSearch:
    """Test basic index_entries and search functionality."""

    def test_search_empty_index_returns_no_matches(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        assert asyncio.run(index.search("transformer")) == []

    def test_search_no_match_returns_empty(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries(
                "arxiv",
                "2106.09685v2",
                "pdf",
                [
                    {"key": "Introduction", "start": 0, "length": 200, "text": "This paper is about cats and dogs."},
                ],
            )
        )
        assert asyncio.run(index.search("transformer")) == []

    def test_search_finds_matching_text(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries(
                "arxiv",
                "2106.09685v2",
                "pdf",
                [
                    {
                        "key": "Introduction",
                        "start": 0,
                        "length": 200,
                        "text": "A transformer architecture for sequence modeling.",
                    },
                ],
            )
        )
        results = asyncio.run(index.search("transformer"))
        assert len(results) == 1
        assert results[0]["provider"] == "arxiv"
        assert results[0]["identifier"] == "2106.09685v2"
        assert results[0]["format"] == "pdf"
        assert results[0]["offset"] == 0
        assert "transformer" in results[0]["snippet"].lower()
        assert isinstance(results[0]["score"], float)

    def test_search_scoped_to_provider(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries(
                "arxiv", "2106.09685v2", "pdf", [{"key": "A", "start": 0, "length": 10, "text": "quantum computing"}]
            )
        )
        asyncio.run(
            index.index_entries(
                "europepmc", "MED:1", "xml", [{"key": "B", "start": 0, "length": 10, "text": "quantum computing"}]
            )
        )
        results = asyncio.run(index.search("quantum", provider="arxiv"))
        assert len(results) == 1
        assert results[0]["provider"] == "arxiv"

    def test_search_scoped_to_identifier_and_format(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries(
                "arxiv",
                "2106.09685v2",
                "pdf",
                [{"key": "A", "start": 0, "length": 10, "text": "graph neural networks"}],
            )
        )
        asyncio.run(
            index.index_entries(
                "arxiv",
                "2106.09685v2",
                "html",
                [{"key": "A", "start": 0, "length": 10, "text": "graph neural networks"}],
            )
        )
        results = asyncio.run(index.search("graph", provider="arxiv", identifier="2106.09685v2", format="pdf"))
        assert len(results) == 1
        assert results[0]["format"] == "pdf"

    def test_search_ranks_more_relevant_entry_first(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries(
                "arxiv", "A", "pdf", [{"key": "A", "start": 0, "length": 10, "text": "cats cats cats cats cats"}]
            )
        )
        asyncio.run(
            index.index_entries(
                "arxiv",
                "B",
                "pdf",
                [
                    {
                        "key": "A",
                        "start": 0,
                        "length": 10,
                        "text": "a document that mentions cats once among many other words entirely unrelated to felines",
                    }
                ],
            )
        )
        results = asyncio.run(index.search("cats"))
        assert [r["identifier"] for r in results] == ["A", "B"]


class TestReplaceOnReindex:
    """Test that indexing the same document replaces previous entries."""

    def test_index_entries_replaces_previous_entries_for_same_document(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries(
                "arxiv", "2106.09685v2", "pdf", [{"key": "Old", "start": 0, "length": 10, "text": "aardvark"}]
            )
        )
        asyncio.run(
            index.index_entries(
                "arxiv", "2106.09685v2", "pdf", [{"key": "New", "start": 0, "length": 10, "text": "zebra"}]
            )
        )
        assert asyncio.run(index.search("aardvark")) == []
        assert len(asyncio.run(index.search("zebra"))) == 1


class TestRemoveDocument:
    """Test remove_document functionality."""

    def test_remove_document_clears_its_entries(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries(
                "arxiv", "2106.09685v2", "pdf", [{"key": "A", "start": 0, "length": 10, "text": "octopus"}]
            )
        )
        asyncio.run(index.remove_document("arxiv", "2106.09685v2", "pdf"))
        assert asyncio.run(index.search("octopus")) == []

    def test_remove_document_only_affects_that_document(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries("arxiv", "A", "pdf", [{"key": "A", "start": 0, "length": 10, "text": "narwhal"}])
        )
        asyncio.run(
            index.index_entries("arxiv", "B", "pdf", [{"key": "A", "start": 0, "length": 10, "text": "narwhal"}])
        )
        asyncio.run(index.remove_document("arxiv", "A", "pdf"))
        results = asyncio.run(index.search("narwhal"))
        assert [r["identifier"] for r in results] == ["B"]

    def test_remove_document_missing_document_is_a_no_op(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(index.remove_document("arxiv", "nope", "pdf"))  # must not raise
