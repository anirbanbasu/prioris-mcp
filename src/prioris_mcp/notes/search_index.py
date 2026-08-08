"""Full-text keyword search over note text, separate from NotesBackend.

See docs/requirement-specification/08-notes-storage.md#notes-searchsqlite3-is-deliberately-minimal--a-departure-from-searchsqlite3s-precedent.
v1 ships one implementation: SQLite + FTS5, at `<notes-root>/notes-search.sqlite3`.
"""

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

from anyio import to_thread

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_search USING fts5(text, id UNINDEXED);
"""


class NotesSearchIndex(ABC):
    """Pluggable keyword search over note text, keyed by note id."""

    @abstractmethod
    async def index_note(self, note_id: str, text: str) -> None:
        """Replace this note's indexed text (insert or reindex)."""

    @abstractmethod
    async def remove_note(self, note_id: str) -> None:
        """Remove a note from the index. A no-op if it isn't present."""

    @abstractmethod
    async def search(self, query: str) -> list[str]:
        """Return matching note ids, ranked by relevance (most relevant first)."""


class SqliteFts5NotesSearchIndex(NotesSearchIndex):
    """SQLite FTS5-backed NotesSearchIndex at `<notes-root>/notes-search.sqlite3`."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return conn

    async def index_note(self, note_id: str, text: str) -> None:
        def _index() -> None:
            with self._connect() as conn:
                conn.execute("DELETE FROM notes_search WHERE id = ?", (note_id,))
                conn.execute("INSERT INTO notes_search (text, id) VALUES (?, ?)", (text, note_id))

        await to_thread.run_sync(_index)

    async def remove_note(self, note_id: str) -> None:
        def _remove() -> None:
            with self._connect() as conn:
                conn.execute("DELETE FROM notes_search WHERE id = ?", (note_id,))

        await to_thread.run_sync(_remove)

    async def search(self, query: str) -> list[str]:
        def _search() -> list[str]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id FROM notes_search WHERE notes_search MATCH ? ORDER BY bm25(notes_search)",
                    (query,),
                ).fetchall()
                return [row["id"] for row in rows]

        return await to_thread.run_sync(_search)
