import asyncio

from prioris_mcp.storage.manifest import DocumentManifest


class TestReplaceLeafRows:
    """Test replace_leaf_rows and related lookup operations."""

    def test_leaf_for_page_missing_returns_none(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        result = asyncio.run(manifest.leaf_for_page("pdf", 1))
        assert result is None

    def test_replace_leaf_rows_then_lookup_by_page(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        asyncio.run(
            manifest.replace_leaf_rows(
                "pdf",
                [
                    {"start": 0, "length": 100},
                    {"start": 100, "length": 150},
                    {"start": 250, "length": 80},
                ],
            )
        )
        assert asyncio.run(manifest.leaf_for_page("pdf", 1)) == {"start": 0, "length": 100}
        assert asyncio.run(manifest.leaf_for_page("pdf", 2)) == {"start": 100, "length": 150}
        assert asyncio.run(manifest.leaf_for_page("pdf", 3)) == {"start": 250, "length": 80}
        assert asyncio.run(manifest.leaf_for_page("pdf", 4)) is None

    def test_replace_leaf_rows_is_idempotent_replace_not_append(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        asyncio.run(manifest.replace_leaf_rows("pdf", [{"start": 0, "length": 50}]))
        asyncio.run(manifest.replace_leaf_rows("pdf", [{"start": 0, "length": 60}, {"start": 60, "length": 40}]))
        assert asyncio.run(manifest.total_pages("pdf")) == 2
        assert asyncio.run(manifest.leaf_for_page("pdf", 1)) == {"start": 0, "length": 60}

    def test_leaf_rows_scoped_per_format(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        asyncio.run(manifest.replace_leaf_rows("pdf", [{"start": 0, "length": 10}]))
        asyncio.run(manifest.replace_leaf_rows("html", [{"start": 0, "length": 999}]))
        assert asyncio.run(manifest.total_pages("pdf")) == 1
        assert asyncio.run(manifest.total_pages("html")) == 1
        assert asyncio.run(manifest.leaf_for_page("pdf", 1)) == {"start": 0, "length": 10}


class TestTotalPages:
    """Test total_pages operations."""

    def test_total_pages_zero_when_no_leaves(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        assert asyncio.run(manifest.total_pages("pdf")) == 0


class TestPageRangeForSpan:
    """Test page_range_for_span operations."""

    def test_page_range_single_page_window(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        asyncio.run(
            manifest.replace_leaf_rows(
                "pdf",
                [
                    {"start": 0, "length": 100},
                    {"start": 100, "length": 100},
                    {"start": 200, "length": 100},
                ],
            )
        )
        assert asyncio.run(manifest.page_range_for_span("pdf", 10, 20)) == (1, 1)

    def test_page_range_window_spanning_two_pages(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        asyncio.run(
            manifest.replace_leaf_rows(
                "pdf",
                [
                    {"start": 0, "length": 100},
                    {"start": 100, "length": 100},
                    {"start": 200, "length": 100},
                ],
            )
        )
        assert asyncio.run(manifest.page_range_for_span("pdf", 90, 30)) == (1, 2)

    def test_page_range_window_at_document_end(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        asyncio.run(manifest.replace_leaf_rows("pdf", [{"start": 0, "length": 100}, {"start": 100, "length": 100}]))
        assert asyncio.run(manifest.page_range_for_span("pdf", 150, 100)) == (2, 2)

    def test_page_range_on_format_with_no_leaf_rows(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        # Call page_range_for_span on a format that has never had replace_leaf_rows called
        result = asyncio.run(manifest.page_range_for_span("pdf", 0, 10))
        assert result == (1, 1)


class TestReplaceChunkRows:
    """Test replace_chunk_rows and related chunk operations."""

    def test_replace_chunk_rows_then_rows_for_search(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        asyncio.run(
            manifest.replace_chunk_rows(
                "pdf",
                [
                    {"key": "Introduction", "start": 0, "length": 50},
                    {"key": "Methods", "start": 50, "length": 80},
                ],
                scheme="heading-bounded-v1",
            )
        )
        rows = asyncio.run(manifest.rows_for_search("pdf"))
        assert rows == [
            {"key": "Introduction", "start": 0, "length": 50},
            {"key": "Methods", "start": 50, "length": 80},
        ]

    def test_replace_chunk_rows_is_idempotent_per_scheme(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        asyncio.run(
            manifest.replace_chunk_rows("pdf", [{"key": "A", "start": 0, "length": 10}], scheme="heading-bounded-v1")
        )
        asyncio.run(
            manifest.replace_chunk_rows("pdf", [{"key": "B", "start": 0, "length": 20}], scheme="heading-bounded-v1")
        )
        rows = asyncio.run(manifest.rows_for_search("pdf"))
        assert rows == [{"key": "B", "start": 0, "length": 20}]


class TestRowsForSearchLeafFallback:
    """Test rows_for_search leaf fallback behavior."""

    def test_rows_for_search_falls_back_to_leaves_when_no_chunks(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        asyncio.run(manifest.replace_leaf_rows("xml", [{"start": 0, "length": 500}]))
        rows = asyncio.run(manifest.rows_for_search("xml"))
        assert rows == [{"key": "1", "start": 0, "length": 500}]

    def test_rows_for_search_prefers_chunks_over_leaves(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        asyncio.run(manifest.replace_leaf_rows("pdf", [{"start": 0, "length": 500}]))
        asyncio.run(
            manifest.replace_chunk_rows(
                "pdf", [{"key": "Intro", "start": 0, "length": 200}], scheme="heading-bounded-v1"
            )
        )
        rows = asyncio.run(manifest.rows_for_search("pdf"))
        assert rows == [{"key": "Intro", "start": 0, "length": 200}]


class TestDeleteFormatAndRowCount:
    """Test delete_format and row_count operations."""

    def test_row_count_zero_on_empty_manifest(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        assert asyncio.run(manifest.row_count()) == 0

    def test_delete_format_removes_only_that_formats_rows(self, tmp_path):
        manifest = DocumentManifest(tmp_path / "manifest.sqlite")
        asyncio.run(manifest.replace_leaf_rows("pdf", [{"start": 0, "length": 10}]))
        asyncio.run(manifest.replace_leaf_rows("html", [{"start": 0, "length": 20}]))
        asyncio.run(manifest.delete_format("pdf"))
        assert asyncio.run(manifest.total_pages("pdf")) == 0
        assert asyncio.run(manifest.total_pages("html")) == 1
        assert asyncio.run(manifest.row_count()) == 1
