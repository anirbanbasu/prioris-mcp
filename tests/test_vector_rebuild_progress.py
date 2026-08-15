from prioris_mcp.vector.rebuild_progress import VectorRebuildProgress


class TestVectorRebuildProgress:
    """Tests for the process-local, in-memory reconciliation progress counter."""

    def test_starts_at_zero_for_both_mechanisms(self):
        progress = VectorRebuildProgress()
        assert progress.documents.total == 0
        assert progress.documents.remaining == 0
        assert progress.notes.total == 0
        assert progress.notes.remaining == 0

    def test_set_documents_total_sets_both_total_and_remaining(self):
        progress = VectorRebuildProgress()
        progress.set_documents_total(5)
        assert progress.documents.total == 5
        assert progress.documents.remaining == 5

    def test_document_done_decrements_remaining_not_total(self):
        progress = VectorRebuildProgress()
        progress.set_documents_total(2)
        progress.document_done()
        assert progress.documents.total == 2
        assert progress.documents.remaining == 1

    def test_document_done_never_goes_negative(self):
        progress = VectorRebuildProgress()
        progress.set_documents_total(0)
        progress.document_done()
        assert progress.documents.remaining == 0

    def test_notes_mirrors_documents(self):
        progress = VectorRebuildProgress()
        progress.set_notes_total(3)
        progress.note_done()
        assert progress.notes.total == 3
        assert progress.notes.remaining == 2

    def test_documents_and_notes_are_independent(self):
        progress = VectorRebuildProgress()
        progress.set_documents_total(1)
        progress.set_notes_total(1)
        progress.document_done()
        assert progress.documents.remaining == 0
        assert progress.notes.remaining == 1
