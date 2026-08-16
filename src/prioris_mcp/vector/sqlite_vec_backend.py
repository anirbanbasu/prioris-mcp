"""sqlite-vec-backed VectorSearchBackend implementation for the document corpus.

See docs/requirement-specification/search/02-vector-search.md#engine-sqlite-vec-asg017 (ADR-00021)
and #vec0-filtering-metadata-columns-not-auxiliary-columns-or-a-join. DDL/KNN syntax verified
against sqlite-vec 0.1.9 (the version resolved by uv.lock): `provider`/`identifier`/`format` are
declared as metadata columns (filterable via `WHERE ... = ?` inside the same KNN query, no join),
the embedding column is `distance_metric=cosine` so `score` matches the requirement spec's "cosine
similarity/distance", and `k` is passed as a `WHERE ... AND k = ?` predicate alongside those
metadata filters - all confirmed against the installed extension directly, not merely assumed from
this plan's sketch.
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import sqlite_vec
from anyio import to_thread

from prioris_mcp.vector.backend import DocumentVectorSearchBackend, IndexStatus, NoteVectorSearchBackend
from prioris_mcp.vector.chunk_splitting import split_oversized_chunk
from prioris_mcp.vector.embedding import EmbeddingBackend

_DEFAULT_MAX_CHUNK_CHARS = 2000

# Empirical safety margin: how many raw KNN rows to fetch per requested result, so collapsing
# same-chunk_id sub-splits (see `search()`) rarely starves `limit` distinct chunks. Not derived
# from any formal bound on sub-split count per chunk - just a multiplier found adequate in
# practice.
_CHUNK_COLLAPSE_OVERFETCH_FACTOR = 4

# SQLite's bound-variable limit (SQLITE_MAX_VARIABLE_NUMBER) is 999 on older builds, 32766 on
# modern default builds - a note_id IN (...) list built from an unbounded corpus-wide id set
# (see SqliteVecNoteBackend.search/count/statuses_for) can exceed either. Kept safely under the
# lower, older bound.
_MAX_BOUND_NOTE_IDS = 896


def _chunked(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _connect_with_vec(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


class SqliteVecDocumentBackend(DocumentVectorSearchBackend):
    """Corpus-wide document-chunk vector index at `<vector-root>/vectors.sqlite3`."""

    def __init__(self, path: Path, embedding_backend: EmbeddingBackend, *, max_chunk_chars: int | None = None) -> None:
        """Initialise the backend.

        Args:
            path: Path to the sqlite-vec-backed database file. Its parent directory is created if
                missing.
            embedding_backend: The injected EmbeddingBackend used to embed indexed text and search
                queries, and whose `model_name`/`dimension` fix the vec0 table's shape and
                staleness comparison.
            max_chunk_chars: Passed straight through to `split_oversized_chunk` for each indexed
                entry. Defaults to `embedding_backend.max_chunk_chars` (a per-model budget derived
                from that model's token-truncation limit) when omitted, rather than one fixed
                value shared across every model.
        """
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_backend = embedding_backend
        self._max_chunk_chars = max_chunk_chars if max_chunk_chars is not None else embedding_backend.max_chunk_chars

    def _connect(self) -> sqlite3.Connection:
        conn = _connect_with_vec(self._path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS document_vectors_status ("
            "provider TEXT NOT NULL, identifier TEXT NOT NULL, format TEXT NOT NULL, "
            "embedded_model TEXT NOT NULL, PRIMARY KEY (provider, identifier, format))"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS document_vectors_meta (id INTEGER PRIMARY KEY CHECK (id = 1))")
        existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(document_vectors_meta)")}
        if "model_name" not in existing_columns:
            # Guarded ADD COLUMN, not a fresh CREATE: an on-disk database from before this change
            # only has a `dimension` column. A NULL model_name here will never equal the currently
            # configured model_name below, so the very next connect after this upgrade always
            # trips the mismatch branch and safely rebuilds - the correct conservative behaviour
            # for a database whose recorded model identity is unknown under the new scheme.
            # Race-safe: concurrent connects can both read PRAGMA before either commits ALTER;
            # wrap in try/except to treat duplicate column as benign (another connection already added it).
            try:
                conn.execute("ALTER TABLE document_vectors_meta ADD COLUMN model_name TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc):
                    raise
        meta_row = conn.execute("SELECT model_name FROM document_vectors_meta WHERE id = 1").fetchone()
        if meta_row is not None and meta_row["model_name"] != self._embedding_backend.model_name:
            conn.execute("DROP TABLE IF EXISTS document_vectors")
            # A status row surviving this drop would let indexed_under()/status() report a
            # document as ready under some prior model even though its vector row is gone -
            # concretely, a rollback (model-a -> model-b -> model-a) would otherwise skip
            # reconciling documents whose model-a status row never got touched by the
            # intervening model-b generation. Wiping every status row here forces status() to
            # `not_built` for anything the drop affected, so it's always re-scheduled.
            conn.execute("DELETE FROM document_vectors_status")
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS document_vectors USING vec0(
                embedding FLOAT[{self._embedding_backend.dimension}] distance_metric=cosine,
                provider TEXT,
                identifier TEXT,
                format TEXT,
                +chunk_id TEXT,
                +span_start INTEGER,
                +text TEXT,
                +embedded_model TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO document_vectors_meta (id, model_name) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET model_name = excluded.model_name",
            (self._embedding_backend.model_name,),
        )
        return conn

    async def index_entries(self, provider: str, identifier: str, format: str, entries: list[dict]) -> None:
        """Replace all indexed vectors for (provider, identifier, format) with `entries`.

        Each entry is first passed through `split_oversized_chunk` so oversized chunks become one
        row per embedding-sized window; every window of one entry shares that entry's own `start`
        as `span_start` - a sub-split has no separate offset of its own.
        """
        windows: list[dict] = []
        for entry in entries:
            for window in split_oversized_chunk(entry["chunk_id"], entry["text"], max_chars=self._max_chunk_chars):
                windows.append({**window, "start": entry["start"]})
        embeddings = [await self._embedding_backend.embed(window["text"]) for window in windows]

        def _write() -> None:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM document_vectors WHERE provider = ? AND identifier = ? AND format = ?",
                    (provider, identifier, format),
                )
                for window, embedding in zip(windows, embeddings, strict=True):
                    conn.execute(
                        "INSERT INTO document_vectors "
                        "(embedding, provider, identifier, format, chunk_id, span_start, text, embedded_model) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            json.dumps(embedding),
                            provider,
                            identifier,
                            format,
                            window["chunk_id"],
                            window["start"],
                            window["text"],
                            self._embedding_backend.model_name,
                        ),
                    )
                if windows:
                    conn.execute(
                        "INSERT INTO document_vectors_status (provider, identifier, format, embedded_model) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT(provider, identifier, format) "
                        "DO UPDATE SET embedded_model = excluded.embedded_model",
                        (provider, identifier, format, self._embedding_backend.model_name),
                    )
                else:
                    conn.execute(
                        "DELETE FROM document_vectors_status WHERE provider = ? AND identifier = ? AND format = ?",
                        (provider, identifier, format),
                    )

        await to_thread.run_sync(_write)

    async def remove_document(self, provider: str, identifier: str, format: str) -> None:
        """Remove every indexed vector for (provider, identifier, format). A no-op if none exist."""

        def _remove() -> None:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM document_vectors WHERE provider = ? AND identifier = ? AND format = ?",
                    (provider, identifier, format),
                )
                conn.execute(
                    "DELETE FROM document_vectors_status WHERE provider = ? AND identifier = ? AND format = ?",
                    (provider, identifier, format),
                )

        await to_thread.run_sync(_remove)

    async def search(
        self,
        query_embedding: list[float],
        *,
        provider: str | None = None,
        identifier: str | None = None,
        format: str | None = None,
        offset: int = 0,
        limit: int = 10,
    ) -> list[dict]:
        """Cosine-similarity KNN search, ranked most-similar first.

        Embeds nothing itself - the caller already has `query_embedding`. Hits sharing the same
        (provider, identifier, format, chunk_id) - sub-splits of one oversized chunk - collapse to
        their single max-scoring row before `limit` is applied, so a caller never sees more than
        one result per chunk. `chunk_id` alone is document-local, not globally unique, so the
        collapse key includes document identity too - otherwise two different documents that
        happen to share a `chunk_id` value could incorrectly collapse into one result.
        """

        def _build_query(k: int) -> tuple[str, list[object]]:
            sql = (
                "SELECT provider, identifier, format, chunk_id, span_start, text, distance "
                "FROM document_vectors WHERE embedding MATCH ? AND k = ?"
            )
            params: list[object] = [json.dumps(query_embedding), k]
            if provider is not None:
                sql += " AND provider = ?"
                params.append(provider)
            if identifier is not None:
                sql += " AND identifier = ?"
                params.append(identifier)
            if format is not None:
                sql += " AND format = ?"
                params.append(format)
            sql += " ORDER BY distance"
            return sql, params

        def _search() -> list[dict]:
            needed = offset + limit
            # Over-fetch before collapsing same-chunk_id sub-splits, so collapsing never starves
            # `offset + limit` distinct chunks below what a caller asked for. The overfetch factor
            # is empirical, not a bound - if an oversized parent chunk has more winning sub-splits
            # than the current window, keep doubling the KNN window (re-querying, since `k` must
            # be re-issued as a new MATCH) until either enough distinct chunks are found or the KNN
            # query itself returns fewer rows than requested (there is nothing left to fetch).
            fetch_k = needed * _CHUNK_COLLAPSE_OVERFETCH_FACTOR
            with self._connect() as conn:
                while True:
                    sql, params = _build_query(fetch_k)
                    rows = conn.execute(sql, params).fetchall()
                    best_per_chunk: dict[tuple[str, str, str, str], sqlite3.Row] = {}
                    for row in rows:
                        key = (row["provider"], row["identifier"], row["format"], row["chunk_id"])
                        existing = best_per_chunk.get(key)
                        if existing is None or row["distance"] < existing["distance"]:
                            best_per_chunk[key] = row
                    if len(best_per_chunk) >= needed or len(rows) < fetch_k:
                        break
                    fetch_k *= 2
            ordered = sorted(best_per_chunk.values(), key=lambda r: r["distance"])[offset : offset + limit]
            return [
                {
                    "provider": row["provider"],
                    "identifier": row["identifier"],
                    "format": row["format"],
                    "chunk_id": row["chunk_id"],
                    "offset": row["span_start"],
                    "snippet": row["text"][:200] + ("..." if len(row["text"]) > 200 else ""),
                    # cosine distance: 0=identical, lower is better - already ORDER BY distance
                    # above, so callers see best-first
                    "score": row["distance"],
                }
                for row in ordered
            ]

        return await to_thread.run_sync(_search)

    async def count(
        self, *, provider: str | None = None, identifier: str | None = None, format: str | None = None
    ) -> int:
        """Count of distinct indexed chunks matching the given filters.

        Counts distinct (provider, identifier, format, chunk_id) tuples rather than raw
        `document_vectors` rows, so an oversized chunk's sub-splits (see `search()`) are counted
        once - matching how many distinct chunks `search()` can ever return for it.
        """

        def _count() -> int:
            sql = (
                "SELECT COUNT(*) AS n FROM ("
                "SELECT DISTINCT provider, identifier, format, chunk_id "
                "FROM document_vectors WHERE 1=1"
            )
            params: list[str] = []
            if provider is not None:
                sql += " AND provider = ?"
                params.append(provider)
            if identifier is not None:
                sql += " AND identifier = ?"
                params.append(identifier)
            if format is not None:
                sql += " AND format = ?"
                params.append(format)
            sql += ")"
            with self._connect() as conn:
                row = conn.execute(sql, params).fetchone()
                return row["n"]

        return await to_thread.run_sync(_count)

    async def status(self, provider: str, identifier: str, format: str) -> IndexStatus:
        """Compare this document's recorded embedded_model against the currently configured one."""

        def _status() -> IndexStatus:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT embedded_model FROM document_vectors_status "
                    "WHERE provider = ? AND identifier = ? AND format = ?",
                    (provider, identifier, format),
                ).fetchone()
            if row is None:
                return "not_built"
            return "ready" if row["embedded_model"] == self._embedding_backend.model_name else "stale"

        return await to_thread.run_sync(_status)

    async def indexed_under(self, model_name: str) -> set[tuple[str, str, str]]:
        """Every (provider, identifier, format) currently recorded as embedded under `model_name`."""

        def _query() -> set[tuple[str, str, str]]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT provider, identifier, format FROM document_vectors_status WHERE embedded_model = ?",
                    (model_name,),
                ).fetchall()
            return {(row["provider"], row["identifier"], row["format"]) for row in rows}

        return await to_thread.run_sync(_query)


class SqliteVecNoteBackend(NoteVectorSearchBackend):
    """Corpus-wide note vector index at `<vector-root>/notes-vectors.sqlite3`."""

    def __init__(self, path: Path, embedding_backend: EmbeddingBackend) -> None:
        """Initialise the backend.

        Args:
            path: Path to the sqlite-vec-backed database file. Its parent directory is created if
                missing.
            embedding_backend: The injected EmbeddingBackend used to embed indexed text and search
                queries, and whose `model_name`/`dimension` fix the vec0 table's shape and
                staleness comparison.
        """
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_backend = embedding_backend

    def _connect(self) -> sqlite3.Connection:
        conn = _connect_with_vec(self._path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS note_vectors_status "
            "(note_id TEXT NOT NULL PRIMARY KEY, embedded_model TEXT NOT NULL)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS note_vectors_meta (id INTEGER PRIMARY KEY CHECK (id = 1))")
        existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(note_vectors_meta)")}
        if "model_name" not in existing_columns:
            # Race-safe: concurrent connects can both read PRAGMA before either commits ALTER;
            # wrap in try/except to treat duplicate column as benign (another connection already added it).
            try:
                conn.execute("ALTER TABLE note_vectors_meta ADD COLUMN model_name TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc):
                    raise
        meta_row = conn.execute("SELECT model_name FROM note_vectors_meta WHERE id = 1").fetchone()
        if meta_row is not None and meta_row["model_name"] != self._embedding_backend.model_name:
            conn.execute("DROP TABLE IF EXISTS note_vectors")
            # See the matching comment in SqliteVecDocumentBackend._connect(): a status row
            # surviving this drop would let indexed_under()/status() report a note as ready under
            # some prior model even though its vector row is gone, letting a model-a -> model-b
            # -> model-a rollback silently skip reconciling it.
            conn.execute("DELETE FROM note_vectors_status")
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS note_vectors USING vec0(
                embedding FLOAT[{self._embedding_backend.dimension}] distance_metric=cosine,
                note_id TEXT,
                +text TEXT,
                +embedded_model TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO note_vectors_meta (id, model_name) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET model_name = excluded.model_name",
            (self._embedding_backend.model_name,),
        )
        return conn

    async def index_note(self, note_id: str, text: str) -> None:
        """Replace this note's indexed vector (insert or reindex). Embeds `text` internally."""
        embedding = await self._embedding_backend.embed(text)

        def _write() -> None:
            with self._connect() as conn:
                conn.execute("DELETE FROM note_vectors WHERE note_id = ?", (note_id,))
                conn.execute(
                    "INSERT INTO note_vectors (embedding, note_id, text, embedded_model) VALUES (?, ?, ?, ?)",
                    (json.dumps(embedding), note_id, text, self._embedding_backend.model_name),
                )
                conn.execute(
                    "INSERT INTO note_vectors_status (note_id, embedded_model) VALUES (?, ?) "
                    "ON CONFLICT(note_id) DO UPDATE SET embedded_model = excluded.embedded_model",
                    (note_id, self._embedding_backend.model_name),
                )

        await to_thread.run_sync(_write)

    async def remove_note(self, note_id: str) -> None:
        """Remove a note's indexed vector. A no-op if it isn't present."""

        def _remove() -> None:
            with self._connect() as conn:
                conn.execute("DELETE FROM note_vectors WHERE note_id = ?", (note_id,))
                conn.execute("DELETE FROM note_vectors_status WHERE note_id = ?", (note_id,))

        await to_thread.run_sync(_remove)

    async def search(
        self, query_embedding: list[float], *, note_ids: list[str] | None = None, offset: int = 0, limit: int = 10
    ) -> list[dict]:
        """Cosine-similarity KNN search over notes, ranked most-similar first.

        `note_ids`, when given, scopes the search to that id set (e.g. notes on one document) and
        is chunked into batches of `_MAX_BOUND_NOTE_IDS` to stay under SQLite's bound-variable
        limit. Each batch is queried for its own top `offset + limit` matches and the batches are
        merged and re-sorted by distance before slicing - a note's rank within its own batch can
        only be <= its rank across the full `note_ids` scope (a subset can't rank it worse), so the
        true global top `offset + limit` is always contained in the union of per-batch top sets.
        """

        def _query(conn: sqlite3.Connection, ids_batch: list[str] | None) -> list[sqlite3.Row]:
            sql = "SELECT note_id, text, distance FROM note_vectors WHERE embedding MATCH ? AND k = ?"
            params: list[object] = [json.dumps(query_embedding), offset + limit]
            if ids_batch is not None:
                placeholders = ", ".join("?" for _ in ids_batch)
                sql += f" AND note_id IN ({placeholders})"
                params.extend(ids_batch)
            sql += " ORDER BY distance"
            return conn.execute(sql, params).fetchall()

        def _search() -> list[dict]:
            with self._connect() as conn:
                if note_ids is None:
                    rows = _query(conn, None)
                else:
                    rows = []
                    for batch in _chunked(note_ids, _MAX_BOUND_NOTE_IDS):
                        rows.extend(_query(conn, batch))
                    rows.sort(key=lambda row: row["distance"])
            ordered = rows[offset : offset + limit]
            return [
                {
                    "note_id": row["note_id"],
                    # cosine distance: 0=identical, lower is better - already ORDER BY distance
                    # above, so callers see best-first
                    "score": row["distance"],
                    "text_preview": row["text"][:200] + ("..." if len(row["text"]) > 200 else ""),
                }
                for row in ordered
            ]

        return await to_thread.run_sync(_search)

    async def count(self, *, note_ids: list[str] | None = None) -> int:
        """Count of indexed notes, optionally scoped to `note_ids`.

        `note_ids` is chunked into batches of `_MAX_BOUND_NOTE_IDS` to stay under SQLite's
        bound-variable limit; per-batch counts are summed since the batches partition `note_ids`
        with no overlap.
        """

        def _count() -> int:
            with self._connect() as conn:
                if note_ids is None:
                    return conn.execute("SELECT COUNT(*) AS n FROM note_vectors").fetchone()["n"]
                total = 0
                for batch in _chunked(note_ids, _MAX_BOUND_NOTE_IDS):
                    placeholders = ", ".join("?" for _ in batch)
                    sql = f"SELECT COUNT(*) AS n FROM note_vectors WHERE note_id IN ({placeholders})"
                    total += conn.execute(sql, batch).fetchone()["n"]
                return total

        return await to_thread.run_sync(_count)

    async def status(self, note_id: str) -> IndexStatus:
        """Compare this note's recorded embedded_model against the currently configured one."""

        def _status() -> IndexStatus:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT embedded_model FROM note_vectors_status WHERE note_id = ?", (note_id,)
                ).fetchone()
            if row is None:
                return "not_built"
            return "ready" if row["embedded_model"] == self._embedding_backend.model_name else "stale"

        return await to_thread.run_sync(_status)

    async def statuses_for(self, note_ids: list[str]) -> dict[str, IndexStatus]:
        """Every given note_id's persisted status, keyed by note_id.

        `note_ids` is chunked into batches of `_MAX_BOUND_NOTE_IDS` (one batched query per chunk)
        to stay under SQLite's bound-variable limit; results are merged since the batches partition
        `note_ids` with no overlap.
        """
        if not note_ids:
            return {}

        def _statuses() -> dict[str, IndexStatus]:
            result: dict[str, IndexStatus] = {}
            with self._connect() as conn:
                for batch in _chunked(note_ids, _MAX_BOUND_NOTE_IDS):
                    placeholders = ", ".join("?" for _ in batch)
                    rows = conn.execute(
                        f"SELECT note_id, embedded_model FROM note_vectors_status WHERE note_id IN ({placeholders})",
                        batch,
                    ).fetchall()
                    result.update(
                        {
                            row["note_id"]: (
                                "ready" if row["embedded_model"] == self._embedding_backend.model_name else "stale"
                            )
                            for row in rows
                        }
                    )
            return result

        return await to_thread.run_sync(_statuses)

    async def has_any_indexed(self, model_name: str) -> bool:
        """Cheap corpus-wide existence check: whether any note is indexed under `model_name`."""

        def _check() -> bool:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM note_vectors_status WHERE embedded_model = ? LIMIT 1", (model_name,)
                ).fetchone()
            return row is not None

        return await to_thread.run_sync(_check)

    async def indexed_under(self, model_name: str) -> set[str]:
        """Every note_id currently recorded as embedded under `model_name`."""

        def _query() -> set[str]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT note_id FROM note_vectors_status WHERE embedded_model = ?", (model_name,)
                ).fetchall()
            return {row["note_id"] for row in rows}

        return await to_thread.run_sync(_query)
