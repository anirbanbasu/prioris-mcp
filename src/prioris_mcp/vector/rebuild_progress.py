"""Process-local, in-memory progress counter for corpus-wide vector-index reconciliation.

Not persisted - a restart re-derives the same "not yet ready" set from persisted vector status and
re-schedules it (see EmbeddingScheduler + PriorisMCP.reconcile_vector_index), the same
self-healing property `building` status already has. Answers "is the rebuild that started when
this process started still going," never a durable job log - see
docs/superpowers/specs/2026-08-15-vector-index-reconciliation-design.md.
"""

from dataclasses import dataclass, field


@dataclass
class _MechanismProgress:
    total: int = 0
    remaining: int = 0


@dataclass
class VectorRebuildProgress:
    """Tracks documents/notes rebuild progress for the reconciliation run started at server startup."""

    documents: _MechanismProgress = field(default_factory=_MechanismProgress)
    notes: _MechanismProgress = field(default_factory=_MechanismProgress)

    def set_documents_total(self, total: int) -> None:
        """Record how many documents this reconciliation run decided need rebuilding."""
        self.documents = _MechanismProgress(total=total, remaining=total)

    def document_done(self) -> None:
        """Mark one document's rebuild as complete."""
        self.documents.remaining = max(0, self.documents.remaining - 1)

    def set_notes_total(self, total: int) -> None:
        """Record how many notes this reconciliation run decided need rebuilding."""
        self.notes = _MechanismProgress(total=total, remaining=total)

    def note_done(self) -> None:
        """Mark one note's rebuild as complete."""
        self.notes.remaining = max(0, self.notes.remaining - 1)
