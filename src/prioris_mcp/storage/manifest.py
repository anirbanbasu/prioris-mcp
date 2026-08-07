"""Per-document SQLite structure: leaf (page-span) and chunk (heading-bounded section) rows.

One manifest.sqlite file per document-hash directory, shared across every format fetched under
that document. See
docs/requirement-specification/02-storage.md#per-document-structure-manifestsqlite-replaces-structurejsonl.
"""

import json
import sqlite3
from pathlib import Path

from anyio import to_thread

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manifest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    format TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('leaf', 'chunk', 'summary')),
    key TEXT,
    provenance TEXT NOT NULL CHECK (provenance IN ('parser', 'llm')),
    scheme TEXT,
    spans TEXT,
    span_start INTEGER GENERATED ALWAYS AS (json_extract(spans, '$[0].start')) VIRTUAL
);
CREATE INDEX IF NOT EXISTS idx_manifest_format_kind_key ON manifest(format, kind, key);
CREATE INDEX IF NOT EXISTS idx_manifest_span_start ON manifest(format, kind, span_start);
"""


class DocumentManifest:
    """SQLite-backed leaf/chunk structure for one document, at `<document-hash>/manifest.sqlite`."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return conn

    async def replace_leaf_rows(self, format: str, leaf_spans: list[dict]) -> None:
        def _replace() -> None:
            with self._connect() as conn:
                conn.execute("DELETE FROM manifest WHERE format = ? AND kind = 'leaf'", (format,))
                conn.executemany(
                    "INSERT INTO manifest (format, kind, key, provenance, scheme, spans) "
                    "VALUES (?, 'leaf', ?, 'parser', NULL, ?)",
                    [
                        (format, str(page), json.dumps([{"start": span["start"], "length": span["length"]}]))
                        for page, span in enumerate(leaf_spans, start=1)
                    ],
                )

        await to_thread.run_sync(_replace)

    async def replace_chunk_rows(self, format: str, chunks: list[dict], scheme: str) -> None:
        def _replace() -> None:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM manifest WHERE format = ? AND kind = 'chunk' AND scheme = ?", (format, scheme)
                )
                conn.executemany(
                    "INSERT INTO manifest (format, kind, key, provenance, scheme, spans) "
                    "VALUES (?, 'chunk', ?, 'parser', ?, ?)",
                    [
                        (
                            format,
                            chunk["key"],
                            scheme,
                            json.dumps([{"start": chunk["start"], "length": chunk["length"]}]),
                        )
                        for chunk in chunks
                    ],
                )

        await to_thread.run_sync(_replace)

    async def leaf_for_page(self, format: str, page: int) -> dict | None:
        def _get() -> dict | None:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT spans FROM manifest WHERE format = ? AND kind = 'leaf' AND key = ?",
                    (format, str(page)),
                ).fetchone()
                if row is None:
                    return None
                spans = json.loads(row["spans"])
                return {"start": spans[0]["start"], "length": spans[0]["length"]}

        return await to_thread.run_sync(_get)

    async def total_pages(self, format: str) -> int:
        def _count() -> int:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM manifest WHERE format = ? AND kind = 'leaf'", (format,)
                ).fetchone()
                return row["n"]

        return await to_thread.run_sync(_count)

    async def page_range_for_span(self, format: str, start: int, length: int) -> tuple[int, int]:
        def _range() -> tuple[int, int]:
            end = start + length
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT key, span_start FROM manifest WHERE format = ? AND kind = 'leaf' ORDER BY span_start",
                    (format,),
                ).fetchall()
                pages = [int(row["key"]) for row in rows if row["span_start"] < end]
                last_page = pages[-1] if pages else (int(rows[0]["key"]) if rows else 1)
                first_page = next((int(row["key"]) for row in reversed(rows) if row["span_start"] <= start), None)
                if first_page is None:
                    first_page = int(rows[0]["key"]) if rows else 1
                return (first_page, int(last_page))

        return await to_thread.run_sync(_range)

    async def rows_for_search(self, format: str) -> list[dict]:
        def _rows() -> list[dict]:
            with self._connect() as conn:
                chunk_rows = conn.execute(
                    "SELECT key, spans FROM manifest WHERE format = ? AND kind = 'chunk' ORDER BY span_start",
                    (format,),
                ).fetchall()
                source_rows = chunk_rows
                if not source_rows:
                    source_rows = conn.execute(
                        "SELECT key, spans FROM manifest WHERE format = ? AND kind = 'leaf' ORDER BY span_start",
                        (format,),
                    ).fetchall()
                result = []
                for row in source_rows:
                    spans = json.loads(row["spans"])
                    result.append({"key": row["key"], "start": spans[0]["start"], "length": spans[0]["length"]})
                return result

        return await to_thread.run_sync(_rows)

    async def delete_format(self, format: str) -> None:
        def _delete() -> None:
            with self._connect() as conn:
                conn.execute("DELETE FROM manifest WHERE format = ?", (format,))

        await to_thread.run_sync(_delete)

    async def row_count(self) -> int:
        def _count() -> int:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM manifest").fetchone()
                return row["n"]

        return await to_thread.run_sync(_count)
