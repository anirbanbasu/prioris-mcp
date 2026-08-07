"""Full-text search over persisted chunks/leaves, separate from StorageBackend.

See docs/requirement-specification/01-architecture.md#searchindex and
docs/requirement-specification/02-storage.md#full-text-search-the-searchsqlite3-index. v1 ships
one implementation: SQLite + FTS5, at `<storage-root>/search.sqlite3`.
"""

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

from anyio import to_thread

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
    text, provider UNINDEXED, identifier UNINDEXED, format UNINDEXED, span_start UNINDEXED
);
"""


class SearchIndex(ABC):
    """Pluggable full-text search over persisted chunk/leaf text."""

    @abstractmethod
    async def index_entries(self, provider: str, identifier: str, format: str, entries: list[dict]) -> None:
        """Replace all indexed entries for (provider, identifier, format) with `entries`.

        Each entry: {"key": str, "start": int, "length": int, "text": str}. A whole-document
        replace, not an incremental upsert - a parse pass produces the complete manifest fresh
        each time.
        """

    @abstractmethod
    async def remove_document(self, provider: str, identifier: str, format: str) -> None:
        """Remove every indexed entry for (provider, identifier, format). A no-op if none exist."""

    @abstractmethod
    async def search(
        self, query: str, *, provider: str | None = None, identifier: str | None = None, format: str | None = None
    ) -> list[dict]:
        """Full-text search, ranked by relevance (most relevant first).

        Returns list of {"provider", "identifier", "format", "snippet", "offset", "score"}.
        """


class SqliteFts5SearchIndex(SearchIndex):
    """SQLite FTS5-backed SearchIndex at `<storage-root>/search.sqlite3`."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return conn

    async def index_entries(self, provider: str, identifier: str, format: str, entries: list[dict]) -> None:
        def _index() -> None:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM search WHERE provider = ? AND identifier = ? AND format = ?",
                    (provider, identifier, format),
                )
                conn.executemany(
                    "INSERT INTO search (text, provider, identifier, format, span_start) VALUES (?, ?, ?, ?, ?)",
                    [(entry["text"], provider, identifier, format, entry["start"]) for entry in entries],
                )

        await to_thread.run_sync(_index)

    async def remove_document(self, provider: str, identifier: str, format: str) -> None:
        def _remove() -> None:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM search WHERE provider = ? AND identifier = ? AND format = ?",
                    (provider, identifier, format),
                )

        await to_thread.run_sync(_remove)

    async def search(
        self, query: str, *, provider: str | None = None, identifier: str | None = None, format: str | None = None
    ) -> list[dict]:
        def _search() -> list[dict]:
            sql = (
                "SELECT provider, identifier, format, span_start, bm25(search) AS score, "
                "snippet(search, 0, '', '', '...', 8) AS snippet "
                "FROM search WHERE search MATCH ?"
            )
            params: list[str] = [query]
            if provider is not None:
                sql += " AND provider = ?"
                params.append(provider)
            if identifier is not None:
                sql += " AND identifier = ?"
                params.append(identifier)
            if format is not None:
                sql += " AND format = ?"
                params.append(format)
            sql += " ORDER BY bm25(search)"
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
                return [
                    {
                        "provider": row["provider"],
                        "identifier": row["identifier"],
                        "format": row["format"],
                        "snippet": row["snippet"],
                        "offset": row["span_start"],
                        "score": row["score"],
                    }
                    for row in rows
                ]

        return await to_thread.run_sync(_search)
