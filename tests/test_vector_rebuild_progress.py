from prioris_mcp.vector.rebuild_progress import VectorRebuildProgress


class TestVectorRebuildProgress:
    """Tests for the process-local, in-memory reconciliation progress counter."""

    def test_starts_at_zero_for_both_mechanisms(self):
        progress = VectorRebuildProgress()
        assert progress.documents.total == 0
        assert progress.documents.pending == 0
        assert progress.documents.succeeded == 0
        assert progress.documents.failed == 0
        assert progress.documents.active is False
        assert progress.notes.total == 0
        assert progress.notes.pending == 0
        assert progress.notes.succeeded == 0
        assert progress.notes.failed == 0
        assert progress.notes.active is False

    def test_set_documents_total_sets_total_and_pending(self):
        progress = VectorRebuildProgress()
        progress.set_documents_total(5)
        assert progress.documents.total == 5
        assert progress.documents.pending == 5
        assert progress.documents.active is True

    def test_document_succeeded_decrements_pending_and_increments_succeeded(self):
        progress = VectorRebuildProgress()
        progress.set_documents_total(2)
        progress.document_succeeded()
        assert progress.documents.total == 2
        assert progress.documents.pending == 1
        assert progress.documents.succeeded == 1
        assert progress.documents.failed == 0

    def test_document_failed_decrements_pending_and_increments_failed(self):
        progress = VectorRebuildProgress()
        progress.set_documents_total(2)
        progress.document_failed()
        assert progress.documents.pending == 1
        assert progress.documents.succeeded == 0
        assert progress.documents.failed == 1

    def test_document_cancelled_decrements_pending_without_counting_as_success_or_failure(self):
        progress = VectorRebuildProgress()
        progress.set_documents_total(2)
        progress.document_cancelled()
        assert progress.documents.pending == 1
        assert progress.documents.succeeded == 0
        assert progress.documents.failed == 0

    def test_document_pending_never_goes_negative(self):
        progress = VectorRebuildProgress()
        progress.set_documents_total(0)
        progress.document_succeeded()
        assert progress.documents.pending == 0

    def test_active_becomes_false_once_pending_reaches_zero(self):
        progress = VectorRebuildProgress()
        progress.set_documents_total(1)
        assert progress.documents.active is True
        progress.document_succeeded()
        assert progress.documents.active is False

    def test_active_is_false_when_some_items_failed_but_none_pending(self):
        """A terminal, partially-failed run must not still read as `active` (Fix 4)."""
        progress = VectorRebuildProgress()
        progress.set_documents_total(2)
        progress.document_succeeded()
        progress.document_failed()
        assert progress.documents.pending == 0
        assert progress.documents.succeeded == 1
        assert progress.documents.failed == 1
        assert progress.documents.active is False

    def test_notes_mirrors_documents(self):
        progress = VectorRebuildProgress()
        progress.set_notes_total(3)
        progress.note_succeeded()
        progress.note_failed()
        progress.note_cancelled()
        assert progress.notes.total == 3
        assert progress.notes.pending == 0
        assert progress.notes.succeeded == 1
        assert progress.notes.failed == 1
        assert progress.notes.active is False

    def test_documents_and_notes_are_independent(self):
        progress = VectorRebuildProgress()
        progress.set_documents_total(1)
        progress.set_notes_total(1)
        progress.document_succeeded()
        assert progress.documents.pending == 0
        assert progress.notes.pending == 1
