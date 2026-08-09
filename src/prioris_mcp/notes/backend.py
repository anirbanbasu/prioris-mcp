"""Interface for persisting user-authored, document-level notes.

See docs/requirement-specification/storage/02-notes-storage.md#notesbackend and 01-architecture.md#notesbackend. `canonical_identifier`
must already be resolved/pinned by the time it reaches this class - resolution is the calling
tool's job (see server.py), not NotesBackend's, the same split StorageBackend already has with
resolve_identifier.
"""

from abc import ABC, abstractmethod

from prioris_mcp.models.notes import Anchor, AuthorFilter, Note, NoteExport, PagedNotes


class NotesBackend(ABC):
    """Persists user-authored notes keyed by (provider, canonical_identifier, format)."""

    @abstractmethod
    async def create(
        self,
        provider: str,
        canonical_identifier: str,
        format: str | None,
        text: str,
        *,
        anchors: list[Anchor] | None = None,
        author_name: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Note:
        """Create a new note. Generates `id`, `created_at`, `updated_at`."""

    @abstractmethod
    async def read(self, note_id: str) -> Note:
        """Return the note with `note_id`.

        Raises:
            FileNotFoundError: no note with this id exists.
        """

    @abstractmethod
    async def update(
        self,
        note_id: str,
        *,
        text: str | None = None,
        anchors: list[Anchor] | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Note:
        """Partially edit an existing note in place; bumps `updated_at`.

        `provider`/`canonical_identifier`/`format`/`author_name` are immutable after creation.

        Raises:
            FileNotFoundError: no note with this id exists.
        """

    @abstractmethod
    async def delete(self, note_id: str) -> bool:
        """Remove the note with `note_id`.

        Returns:
            True if a matching note was found and removed, False otherwise.
        """

    @abstractmethod
    async def search(
        self,
        *,
        provider: str | None = None,
        canonical_identifier: str | None = None,
        format: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        keyword: str | None = None,
        author_filter: AuthorFilter = AuthorFilter.ANY,
        author_name: str | None = None,
        tags_all: list[str] | None = None,
        tags_any: list[str] | None = None,
        tags_exclude: list[str] | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> PagedNotes:
        """Search/list notes. No filters at all is "list everything," paged.

        Raises:
            ValueError: `author_filter == NAMED` without `author_name`, or vice versa;
                or `canonical_identifier` given without `provider`.
        """

    @abstractmethod
    async def export(self, note_id: str) -> NoteExport:
        """Return `note_id`'s file representation for the caller to write to disk itself.

        Raises:
            FileNotFoundError: no note with this id exists.
        """
