from prioris_mcp.vector.chunk_splitting import split_oversized_chunk


class TestSplitOversizedChunk:
    """Test split_oversized_chunk function for chunk splitting behavior."""

    def test_chunk_under_limit_yields_one_unchanged_window(self):
        windows = split_oversized_chunk("chunk-1", "a short section", max_chars=1000)
        assert len(windows) == 1
        assert windows[0] == {"chunk_id": "chunk-1", "text": "a short section", "sub_index": 0}

    def test_oversized_chunk_splits_into_multiple_windows(self):
        text = "word " * 500  # well over any small max_chars
        windows = split_oversized_chunk("chunk-1", text, max_chars=200)
        assert len(windows) > 1
        assert all(w["chunk_id"] == "chunk-1" for w in windows)

    def test_windows_are_indexed_sequentially_from_zero(self):
        text = "word " * 500
        windows = split_oversized_chunk("chunk-1", text, max_chars=200)
        assert [w["sub_index"] for w in windows] == list(range(len(windows)))

    def test_windows_concatenate_back_to_the_original_text(self):
        text = "word " * 500
        windows = split_oversized_chunk("chunk-1", text, max_chars=200)
        assert "".join(w["text"] for w in windows) == text

    def test_each_window_is_at_most_max_chars(self):
        text = "word " * 500
        windows = split_oversized_chunk("chunk-1", text, max_chars=200)
        assert all(len(w["text"]) <= 200 for w in windows)

    def test_empty_text_yields_one_empty_window(self):
        windows = split_oversized_chunk("chunk-1", "", max_chars=1000)
        assert windows == [{"chunk_id": "chunk-1", "text": "", "sub_index": 0}]
