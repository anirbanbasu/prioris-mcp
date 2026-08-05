"""Top-level SQLite index over every persisted (provider, identifier, format, artefact) entry.

See docs/requirement-specification/02-storage.md#the-catalogue-cataloguesqlite. Replaces
per-entry `.json`-manifest globbing with one indexed table, giving list/delete/find O(1)
lookups instead of O(n) file reads.
"""

from __future__ import annotations

import builtins
import sqlite3
from pathlib import Path

from anyio import to_thread

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    provider TEXT NOT NULL,
    canonical_identifier TEXT NOT NULL,
    original_identifier TEXT,
    public_identifier TEXT,
    format TEXT NOT NULL,
    artefact TEXT NOT NULL CHECK (artefact IN ('document', 'markdown')),
    size_bytes INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    external_identifier TEXT GENERATED ALWAYS AS (COALESCE(public_identifier, canonical_identifier)) VIRTUAL,
    PRIMARY KEY (provider, canonical_identifier, format, artefact)
);
CREATE INDEX IF NOT EXISTS idx_entries_provider_format ON entries(provider, format);
CREATE INDEX IF NOT EXISTS idx_entries_external_identifier ON entries(provider, external_identifier, format);
"""

_ROW_COLUMNS = (
    "provider",
    "canonical_identifier",
    "original_identifier",
    "public_identifier",
    "format",
    "artefact",
    "size_bytes",
    "recorded_at",
)


class Catalogue:
    """SQLite-backed index at `<storage-root>/catalogue.sqlite`."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return conn

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> dict:
        return {column: row[column] for column in _ROW_COLUMNS}

    async def upsert(self, entry: dict) -> None:
        def _upsert() -> None:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO entries
                        (provider, canonical_identifier, original_identifier, public_identifier,
                         format, artefact, size_bytes, recorded_at)
                    VALUES (:provider, :canonical_identifier, :original_identifier, :public_identifier,
                            :format, :artefact, :size_bytes, :recorded_at)
                    ON CONFLICT (provider, canonical_identifier, format, artefact) DO UPDATE SET
                        original_identifier = excluded.original_identifier,
                        public_identifier = excluded.public_identifier,
                        size_bytes = excluded.size_bytes,
                        recorded_at = excluded.recorded_at
                    """,
                    entry,
                )

        await to_thread.run_sync(_upsert)

    async def get(self, provider: str, canonical_identifier: str, format: str, artefact: str) -> dict | None:
        def _get() -> dict | None:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM entries WHERE provider = ? AND canonical_identifier = ? "
                    "AND format = ? AND artefact = ?",
                    (provider, canonical_identifier, format, artefact),
                ).fetchone()
                return self._row_to_entry(row) if row is not None else None

        return await to_thread.run_sync(_get)

    async def list(self, provider: str | None = None, format: str | None = None) -> builtins.list[dict]:
        def _list() -> list[dict]:
            query = "SELECT * FROM entries WHERE 1=1"
            params: list[str] = []
            if provider is not None:
                query += " AND provider = ?"
                params.append(provider)
            if format is not None:
                query += " AND format = ?"
                params.append(format)
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
                return [self._row_to_entry(row) for row in rows]

        return await to_thread.run_sync(_list)

    async def remove(self, provider: str, external_identifier: str, format: str, artefact: str) -> bool:
        def _remove() -> bool:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM entries WHERE provider = ? AND external_identifier = ? "
                    "AND format = ? AND artefact = ?",
                    (provider, external_identifier, format, artefact),
                )
                return cursor.rowcount > 0

        return await to_thread.run_sync(_remove)

    async def remove_all_artefacts(self, provider: str, canonical_identifier: str, format: str) -> builtins.list[str]:
        def _remove_all() -> list[str]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT artefact FROM entries WHERE provider = ? AND canonical_identifier = ? AND format = ?",
                    (provider, canonical_identifier, format),
                ).fetchall()
                conn.execute(
                    "DELETE FROM entries WHERE provider = ? AND canonical_identifier = ? AND format = ?",
                    (provider, canonical_identifier, format),
                )
                return [row["artefact"] for row in rows]

        return await to_thread.run_sync(_remove_all)

    async def find_by_external_identifier(self, provider: str, external_identifier: str, format: str) -> dict | None:
        def _find() -> dict | None:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM entries WHERE provider = ? AND external_identifier = ? AND format = ? LIMIT 1",
                    (provider, external_identifier, format),
                ).fetchone()
                return self._row_to_entry(row) if row is not None else None

        return await to_thread.run_sync(_find)

    async def count_formats(self, provider: str, canonical_identifier: str) -> int:
        def _count() -> int:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT format) AS n FROM entries WHERE provider = ? AND canonical_identifier = ?",
                    (provider, canonical_identifier),
                ).fetchone()
                return row["n"]

        return await to_thread.run_sync(_count)
