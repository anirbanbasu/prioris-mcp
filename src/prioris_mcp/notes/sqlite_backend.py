"""SQLite-backed NotesBackend at `<notes-root>/notes.sqlite`.

See docs/requirement-specification/08-notes-storage.md#storage-layout.
"""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from anyio import to_thread
from pydantic import TypeAdapter

from prioris_mcp.models.notes import Anchor, Note, NoteExport, PagedNotes
from prioris_mcp.notes.backend import NotesBackend

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    canonical_identifier TEXT NOT NULL,
    format TEXT,
    text TEXT NOT NULL,
    anchors TEXT NOT NULL DEFAULT '[]',
    author_name TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_provider_identifier_format
    ON notes(provider, canonical_identifier, format);
CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at);
"""

_ANCHOR_LIST_ADAPTER: TypeAdapter[list[Anchor]] = TypeAdapter(list[Anchor])


def _row_to_note(row: sqlite3.Row) -> Note:
    return Note(
        id=row["id"],
        provider=row["provider"],
        canonical_identifier=row["canonical_identifier"],
        format=row["format"],
        text=row["text"],
        anchors=_ANCHOR_LIST_ADAPTER.validate_json(row["anchors"]),
        author_name=row["author_name"],
        tags=json.loads(row["tags"]),
        metadata=json.loads(row["metadata"]) if row["metadata"] is not None else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SqliteNotesBackend(NotesBackend):
    """SQLite-backed NotesBackend; `notes.sqlite` is the durable source of truth."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return conn

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
        note_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        anchors = anchors or []
        tags = tags or []

        def _create() -> None:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO notes
                        (id, provider, canonical_identifier, format, text, anchors, author_name,
                         tags, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        note_id,
                        provider,
                        canonical_identifier,
                        format,
                        text,
                        _ANCHOR_LIST_ADAPTER.dump_json(anchors).decode(),
                        author_name,
                        json.dumps(tags),
                        json.dumps(metadata) if metadata is not None else None,
                        now,
                        now,
                    ),
                )

        await to_thread.run_sync(_create)
        return Note(
            id=note_id,
            provider=provider,
            canonical_identifier=canonical_identifier,
            format=format,
            text=text,
            anchors=anchors,
            author_name=author_name,
            tags=tags,
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )

    async def read(self, note_id: str) -> Note:
        def _read() -> Note:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
                if row is None:
                    raise FileNotFoundError(note_id)
                return _row_to_note(row)

        return await to_thread.run_sync(_read)

    async def update(self, note_id, *, text=None, anchors=None, tags=None, metadata=None) -> Note:
        raise NotImplementedError  # implemented in Task 6

    async def delete(self, note_id: str) -> bool:
        raise NotImplementedError  # implemented in Task 6

    async def search(self, **kwargs) -> PagedNotes:
        raise NotImplementedError  # implemented in Tasks 7-8

    async def export(self, note_id: str) -> NoteExport:
        raise NotImplementedError  # implemented in Task 9
