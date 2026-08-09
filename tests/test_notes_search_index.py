import asyncio

from prioris_mcp.notes.search_index import SqliteFts5NotesSearchIndex


class TestSqliteFts5NotesSearchIndexIndexAndSearch:
    """Test indexing and search functionality of SqliteFts5NotesSearchIndex."""

    def test_search_empty_index_returns_no_results(self, tmp_path):
        index = SqliteFts5NotesSearchIndex(tmp_path / "notes-search.sqlite3")
        result = asyncio.run(index.search("anything"))
        assert result == []

    def test_indexed_note_is_findable_by_keyword(self, tmp_path):
        index = SqliteFts5NotesSearchIndex(tmp_path / "notes-search.sqlite3")
        asyncio.run(index.index_note("note-1", "the control group showed a latency increase"))
        result = asyncio.run(index.search("latency"))
        assert result == ["note-1"]

    def test_search_ranks_more_relevant_match_first(self, tmp_path):
        index = SqliteFts5NotesSearchIndex(tmp_path / "notes-search.sqlite3")
        asyncio.run(index.index_note("note-1", "latency latency latency, a very latency-focused note"))
        asyncio.run(index.index_note("note-2", "this note mentions latency once, in passing"))
        result = asyncio.run(index.search("latency"))
        assert result == ["note-1", "note-2"]

    def test_reindexing_same_id_replaces_previous_text(self, tmp_path):
        index = SqliteFts5NotesSearchIndex(tmp_path / "notes-search.sqlite3")
        asyncio.run(index.index_note("note-1", "about apples"))
        asyncio.run(index.index_note("note-1", "about oranges"))
        assert asyncio.run(index.search("apples")) == []
        assert asyncio.run(index.search("oranges")) == ["note-1"]


class TestSqliteFts5NotesSearchIndexRemove:
    """Test note removal functionality of SqliteFts5NotesSearchIndex."""

    def test_remove_note_drops_it_from_search_results(self, tmp_path):
        index = SqliteFts5NotesSearchIndex(tmp_path / "notes-search.sqlite3")
        asyncio.run(index.index_note("note-1", "about apples"))
        asyncio.run(index.remove_note("note-1"))
        assert asyncio.run(index.search("apples")) == []

    def test_remove_nonexistent_note_is_a_no_op(self, tmp_path):
        index = SqliteFts5NotesSearchIndex(tmp_path / "notes-search.sqlite3")
        asyncio.run(index.remove_note("does-not-exist"))
