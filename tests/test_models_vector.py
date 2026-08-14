from prioris_mcp.models.vector import NoteVectorSearchMatch, VectorSearchMatch


class TestVectorSearchMatch:
    """Construction tests for VectorSearchMatch."""

    def test_construct_with_all_fields(self):
        match = VectorSearchMatch(
            provider="arxiv",
            identifier="2106.09685v2",
            format="pdf",
            chunk_id="c1",
            offset=0,
            snippet="...",
            score=0.12,
        )
        assert match.format_ == "pdf"


class TestNoteVectorSearchMatch:
    """Construction tests for NoteVectorSearchMatch."""

    def test_construct_with_all_fields(self):
        match = NoteVectorSearchMatch(note_id="note-1", score=0.4, text_preview="a preview")
        assert match.note_id == "note-1"
