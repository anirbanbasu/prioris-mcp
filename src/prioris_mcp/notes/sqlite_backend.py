"""SQLite-backed NotesBackend at `<notes-root>/notes.sqlite`.

See docs/requirement-specification/storage/02-notes-storage.md#storage-layout.
"""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from anyio import to_thread
from pydantic import TypeAdapter

from prioris_mcp.models.notes import Anchor, AuthorFilter, Note, NoteExport, PagedNotes
from prioris_mcp.notes.backend import NotesBackend
from prioris_mcp.notes.search_index import NotesSearchIndex

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


def _normalise_date_boundary(value: str | None, field_name: str) -> str | None:
    """Parse an ISO 8601 date/datetime string and normalise it to a UTC isoformat string.

    `created_at` is always stored as `datetime.now(UTC).isoformat()`; a lexicographic TEXT
    comparison against it is only chronologically correct if the compared value is also UTC and
    rendered the same way - a naive datetime, a `Z` suffix, or a non-UTC offset would compare
    wrong without this normalisation.
    """
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid ISO 8601 date/datetime: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat()


class SqliteNotesBackend(NotesBackend):
    """SQLite-backed NotesBackend; `notes.sqlite` is the durable source of truth."""

    def __init__(self, path: Path, search_index: NotesSearchIndex) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._search_index = search_index

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
        await self._search_index.index_note(note_id, text)
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
                    raise FileNotFoundError(f"note not found: {note_id!r}")
                return _row_to_note(row)

        return await to_thread.run_sync(_read)

    async def update(
        self,
        note_id: str,
        *,
        text: str | None = None,
        anchors: list[Anchor] | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Note:
        now = datetime.now(UTC).isoformat()

        def _update() -> Note:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
                if row is None:
                    raise FileNotFoundError(f"note not found: {note_id!r}")
                new_text = text if text is not None else row["text"]
                new_anchors_json = (
                    _ANCHOR_LIST_ADAPTER.dump_json(anchors).decode() if anchors is not None else row["anchors"]
                )
                new_tags_json = json.dumps(tags) if tags is not None else row["tags"]
                new_metadata_json = json.dumps(metadata) if metadata is not None else row["metadata"]
                conn.execute(
                    "UPDATE notes SET text = ?, anchors = ?, tags = ?, metadata = ?, updated_at = ? WHERE id = ?",
                    (new_text, new_anchors_json, new_tags_json, new_metadata_json, now, note_id),
                )
                updated_row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
                return _row_to_note(updated_row)

        updated = await to_thread.run_sync(_update)
        if text is not None:
            await self._search_index.index_note(note_id, updated.text)
        return updated

    async def delete(self, note_id: str) -> bool:
        def _delete() -> bool:
            with self._connect() as conn:
                cursor = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
                return cursor.rowcount > 0

        removed = await to_thread.run_sync(_delete)
        if removed:
            await self._search_index.remove_note(note_id)
        return removed

    def _build_search_where_and_params(
        self,
        provider: str | None,
        canonical_identifier: str | None,
        format: str | None,
        date_from: str | None,
        date_to: str | None,
        author_filter: AuthorFilter,
        author_name: str | None,
        tags_all: list[str] | None,
        tags_any: list[str] | None,
        tags_exclude: list[str] | None,
    ) -> tuple[str, list[str]]:
        """Build WHERE clause and params list for search query.

        Returns a tuple of (where_clause, params_list).
        """
        where = "WHERE 1=1"
        params: list[str] = []
        if provider is not None:
            where += " AND provider = ?"
            params.append(provider)
        if canonical_identifier is not None:
            where += " AND canonical_identifier = ?"
            params.append(canonical_identifier)
        if format is not None:
            where += " AND format = ?"
            params.append(format)
        if date_from is not None:
            where += " AND created_at >= ?"
            params.append(date_from)
        if date_to is not None:
            where += " AND created_at <= ?"
            params.append(date_to)
        if author_filter == AuthorFilter.MINE:
            where += " AND author_name IS NULL"
        elif author_filter == AuthorFilter.NAMED:
            where += " AND author_name = ?"
            params.append(author_name)
        for tag in tags_all or []:
            where += " AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value = ?)"
            params.append(tag)
        if tags_any:
            placeholders = ", ".join("?" for _ in tags_any)
            where += f" AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value IN ({placeholders}))"
            params.extend(tags_any)
        if tags_exclude:
            placeholders = ", ".join("?" for _ in tags_exclude)
            where += f" AND NOT EXISTS (SELECT 1 FROM json_each(tags) WHERE value IN ({placeholders}))"
            params.extend(tags_exclude)
        return where, params

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
        if author_filter == AuthorFilter.NAMED and author_name is None:
            raise ValueError("author_filter=NAMED requires author_name")
        if author_filter != AuthorFilter.NAMED and author_name is not None:
            raise ValueError("author_name is only used when author_filter=NAMED")
        if canonical_identifier is not None and provider is None:
            raise ValueError(
                "canonical_identifier requires provider — canonical identifiers are only unique within a provider's own scheme"
            )
        date_from = _normalise_date_boundary(date_from, "date_from")
        date_to = _normalise_date_boundary(date_to, "date_to")
        where, params = self._build_search_where_and_params(
            provider,
            canonical_identifier,
            format,
            date_from,
            date_to,
            author_filter,
            author_name,
            tags_all,
            tags_any,
            tags_exclude,
        )

        def _search() -> PagedNotes:
            with self._connect() as conn:
                total = conn.execute(f"SELECT COUNT(*) AS n FROM notes {where}", params).fetchone()["n"]
                rows = conn.execute(
                    f"SELECT * FROM notes {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (*params, limit, offset),
                ).fetchall()
                notes = [_row_to_note(row) for row in rows]
                return PagedNotes(
                    notes=notes, offset=offset, limit=limit, total=total, has_more=offset + len(notes) < total
                )

        if keyword is None:
            return await to_thread.run_sync(_search)

        matching_ids = await self._search_index.search(keyword)

        def _search_by_ids() -> PagedNotes:
            if not matching_ids:
                return PagedNotes(notes=[], offset=offset, limit=limit, total=0, has_more=False)
            placeholders = ", ".join("?" for _ in matching_ids)
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT * FROM notes {where} AND id IN ({placeholders})", (*params, *matching_ids)
                ).fetchall()
            structurally_matching_ids = {row["id"] for row in rows}
            by_id = {row["id"]: _row_to_note(row) for row in rows}
            ordered_ids = [note_id for note_id in matching_ids if note_id in structurally_matching_ids]
            total = len(ordered_ids)
            page_ids = ordered_ids[offset : offset + limit]
            return PagedNotes(
                notes=[by_id[note_id] for note_id in page_ids],
                offset=offset,
                limit=limit,
                total=total,
                has_more=offset + len(page_ids) < total,
            )

        return await to_thread.run_sync(_search_by_ids)

    async def export(self, note_id: str) -> NoteExport:
        note = await self.read(note_id)
        frontmatter = note.model_dump(mode="json", by_alias=True, exclude={"text"})
        return NoteExport(
            suggested_filename=f"{note.id}.md",
            frontmatter=frontmatter,
            markdown_body=note.text,
        )
