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


class TestHasEntries:
    """Test has_entries existence-only check."""

    def test_has_entries_true_after_indexing(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries("arxiv", "2106.09685v2", "pdf", [{"key": "A", "start": 0, "length": 10, "text": "x"}])
        )
        assert asyncio.run(index.has_entries("arxiv", "2106.09685v2", "pdf")) is True

    def test_has_entries_false_before_indexing(self, tmp_path):
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        assert asyncio.run(index.has_entries("arxiv", "2106.09685v2", "pdf")) is False


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


class TestSearchPagination:
    """Test search pagination with offset and limit."""

    def test_search_with_default_limit_50(self, tmp_path):
        """Search without explicit limit uses default limit of 50."""
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        entries = [
            {
                "key": f"Entry{i}",
                "start": i,
                "length": 10,
                "text": f"This document contains keyword repeated {i} times.",
            }
            for i in range(30)
        ]
        asyncio.run(index.index_entries("arxiv", "doc1", "pdf", entries))
        results = asyncio.run(index.search("keyword"))
        assert len(results) == 30

    def test_search_with_custom_limit(self, tmp_path):
        """Search respects custom limit parameter."""
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        entries = [
            {
                "key": f"Entry{i}",
                "start": i,
                "length": 10,
                "text": f"This document contains keyword repeated {i} times.",
            }
            for i in range(30)
        ]
        asyncio.run(index.index_entries("arxiv", "doc1", "pdf", entries))
        results = asyncio.run(index.search("keyword", limit=10))
        assert len(results) == 10

    def test_search_with_offset_and_limit(self, tmp_path):
        """Search with offset skips first N results."""
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        entries = [
            {
                "key": f"Entry{i}",
                "start": i,
                "length": 10,
                "text": f"This document contains keyword repeated {i} times.",
            }
            for i in range(30)
        ]
        asyncio.run(index.index_entries("arxiv", "doc1", "pdf", entries))

        # Get first page
        page1 = asyncio.run(index.search("keyword", offset=0, limit=10))
        assert len(page1) == 10
        page1_offsets = [r["offset"] for r in page1]

        # Get second page
        page2 = asyncio.run(index.search("keyword", offset=10, limit=10))
        assert len(page2) == 10
        page2_offsets = [r["offset"] for r in page2]

        # Pages should not overlap
        assert len(set(page1_offsets) & set(page2_offsets)) == 0

    def test_search_offset_beyond_results_returns_empty(self, tmp_path):
        """Search with offset beyond available results returns empty list."""
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries("arxiv", "doc1", "pdf", [{"key": "Entry", "start": 0, "length": 10, "text": "keyword"}])
        )
        results = asyncio.run(index.search("keyword", offset=100, limit=10))
        assert results == []

    def test_search_pagination_respects_filters(self, tmp_path):
        """Search pagination works correctly with provider/identifier/format filters."""
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        # Add entries to multiple documents
        for doc_id in range(1, 4):
            entries = [{"key": f"Entry{i}", "start": i, "length": 10, "text": "keyword"} for i in range(5)]
            asyncio.run(index.index_entries("arxiv", f"doc{doc_id}", "pdf", entries))

        # Search with provider filter and limit
        results = asyncio.run(index.search("keyword", provider="arxiv", limit=5))
        assert len(results) == 5
        assert all(r["provider"] == "arxiv" for r in results)

        # Search with identifier filter and pagination
        results_page1 = asyncio.run(index.search("keyword", identifier="doc1", offset=0, limit=3))
        results_page2 = asyncio.run(index.search("keyword", identifier="doc1", offset=3, limit=3))
        assert len(results_page1) == 3
        assert len(results_page2) == 2
        assert all(r["identifier"] == "doc1" for r in results_page1)
        assert all(r["identifier"] == "doc1" for r in results_page2)


class TestCount:
    """Test count() method."""

    def test_count_empty_index_returns_zero(self, tmp_path):
        """Count on empty index returns 0."""
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        count = asyncio.run(index.count("keyword"))
        assert count == 0

    def test_count_no_match_returns_zero(self, tmp_path):
        """Count with no matching documents returns 0."""
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries(
                "arxiv", "doc1", "pdf", [{"key": "A", "start": 0, "length": 10, "text": "cats and dogs"}]
            )
        )
        count = asyncio.run(index.count("transformer"))
        assert count == 0

    def test_count_matches_search_row_count_when_unpaged(self, tmp_path):
        """Count equals number of search results when unpaged."""
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        entries = [{"key": f"Entry{i}", "start": i, "length": 10, "text": "keyword"} for i in range(25)]
        asyncio.run(index.index_entries("arxiv", "doc1", "pdf", entries))

        count = asyncio.run(index.count("keyword"))
        # Fetch with a large limit to get all results
        results = asyncio.run(index.search("keyword", limit=1000))
        assert count == len(results)
        assert count == 25

    def test_count_with_provider_filter(self, tmp_path):
        """Count respects provider filter."""
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries("arxiv", "doc1", "pdf", [{"key": "A", "start": 0, "length": 10, "text": "keyword"}])
        )
        asyncio.run(
            index.index_entries("europepmc", "doc2", "xml", [{"key": "B", "start": 0, "length": 10, "text": "keyword"}])
        )

        arxiv_count = asyncio.run(index.count("keyword", provider="arxiv"))
        total_count = asyncio.run(index.count("keyword"))
        assert arxiv_count == 1
        assert total_count == 2

    def test_count_with_identifier_and_format_filters(self, tmp_path):
        """Count respects identifier and format filters."""
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        asyncio.run(
            index.index_entries(
                "arxiv",
                "doc1",
                "pdf",
                [
                    {"key": "A", "start": 0, "length": 10, "text": "keyword"},
                    {"key": "B", "start": 10, "length": 10, "text": "keyword"},
                ],
            )
        )
        asyncio.run(
            index.index_entries(
                "arxiv",
                "doc1",
                "html",
                [{"key": "C", "start": 0, "length": 10, "text": "keyword"}],
            )
        )

        pdf_count = asyncio.run(index.count("keyword", identifier="doc1", format="pdf"))
        html_count = asyncio.run(index.count("keyword", identifier="doc1", format="html"))
        total_count = asyncio.run(index.count("keyword", identifier="doc1"))

        assert pdf_count == 2
        assert html_count == 1
        assert total_count == 3

    def test_count_stays_constant_across_pages(self, tmp_path):
        """Count remains the same regardless of pagination."""
        index = SqliteFts5SearchIndex(tmp_path / "search.sqlite3")
        entries = [{"key": f"Entry{i}", "start": i, "length": 10, "text": "keyword"} for i in range(35)]
        asyncio.run(index.index_entries("arxiv", "doc1", "pdf", entries))

        total_count = asyncio.run(index.count("keyword"))

        # Fetch across multiple pages
        page1 = asyncio.run(index.search("keyword", offset=0, limit=10))
        page2 = asyncio.run(index.search("keyword", offset=10, limit=10))
        page3 = asyncio.run(index.search("keyword", offset=20, limit=10))
        page4 = asyncio.run(index.search("keyword", offset=30, limit=10))

        paginated_count = len(page1) + len(page2) + len(page3) + len(page4)
        assert total_count == paginated_count
        assert total_count == 35
