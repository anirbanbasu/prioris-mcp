"""Process-local, in-memory progress counter for corpus-wide vector-index reconciliation.

Not persisted - a restart re-derives the same "not yet ready" set from persisted vector status and
re-schedules it (see EmbeddingScheduler + PriorisMCP.reconcile_vector_index), the same
self-healing property `building` status already has. Answers "is the rebuild that started when
this process started still going," never a durable job log - see
docs/superpowers/specs/2026-08-15-vector-index-reconciliation-design.md.

`succeeded`/`failed` (not just `pending`/`total`) exist so a caller can tell "still running" from
"stuck with N failures" rather than `remaining` staying permanently nonzero with no explanation -
see docs/requirement-specification/ADR/00031-rebuild-progress-failure-visibility.md.
"""

from dataclasses import dataclass, field


@dataclass
class _MechanismProgress:
    total: int = 0
    pending: int = 0
    succeeded: int = 0
    failed: int = 0

    @property
    def active(self) -> bool:
        """Whether this mechanism still has unresolved work from the current reconciliation run."""
        return self.pending > 0


@dataclass
class VectorRebuildProgress:
    """Tracks documents/notes rebuild progress for the reconciliation run started at server startup."""

    documents: _MechanismProgress = field(default_factory=_MechanismProgress)
    notes: _MechanismProgress = field(default_factory=_MechanismProgress)

    def set_documents_total(self, total: int) -> None:
        """Record how many documents this reconciliation run decided need rebuilding."""
        self.documents = _MechanismProgress(total=total, pending=total)

    def document_succeeded(self) -> None:
        """Mark one document's rebuild as a completed, successful re-embed."""
        self.documents.pending = max(0, self.documents.pending - 1)
        self.documents.succeeded += 1

    def document_failed(self) -> None:
        """Mark one document's rebuild as a completed, failed re-embed."""
        self.documents.pending = max(0, self.documents.pending - 1)
        self.documents.failed += 1

    def document_cancelled(self) -> None:
        """Mark one document's rebuild as abandoned (shutdown, or deleted mid-flight) - neither success nor failure."""
        self.documents.pending = max(0, self.documents.pending - 1)

    def set_notes_total(self, total: int) -> None:
        """Record how many notes this reconciliation run decided need rebuilding."""
        self.notes = _MechanismProgress(total=total, pending=total)

    def note_succeeded(self) -> None:
        """Mark one note's rebuild as a completed, successful re-embed."""
        self.notes.pending = max(0, self.notes.pending - 1)
        self.notes.succeeded += 1

    def note_failed(self) -> None:
        """Mark one note's rebuild as a completed, failed re-embed."""
        self.notes.pending = max(0, self.notes.pending - 1)
        self.notes.failed += 1

    def note_cancelled(self) -> None:
        """Mark one note's rebuild as abandoned (shutdown, or deleted mid-flight) - neither success nor failure."""
        self.notes.pending = max(0, self.notes.pending - 1)
