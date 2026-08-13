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
from pathlib import Path

import sqlite_vec
from anyio import to_thread

from prioris_mcp.vector.backend import DocumentVectorSearchBackend, IndexStatus
from prioris_mcp.vector.chunk_splitting import split_oversized_chunk
from prioris_mcp.vector.embedding import EmbeddingBackend

_DEFAULT_MAX_CHUNK_CHARS = 2000


def _connect_with_vec(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


class SqliteVecDocumentBackend(DocumentVectorSearchBackend):
    """Corpus-wide document-chunk vector index at `<vector-root>/vectors.sqlite3`."""

    def __init__(
        self, path: Path, embedding_backend: EmbeddingBackend, *, max_chunk_chars: int = _DEFAULT_MAX_CHUNK_CHARS
    ) -> None:
        """Initialise the backend.

        Args:
            path: Path to the sqlite-vec-backed database file. Its parent directory is created if
                missing.
            embedding_backend: The injected EmbeddingBackend used to embed indexed text and search
                queries, and whose `model_name`/`dimension` fix the vec0 table's shape and
                staleness comparison.
            max_chunk_chars: Passed straight through to `split_oversized_chunk` for each indexed
                entry.
        """
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_backend = embedding_backend
        self._max_chunk_chars = max_chunk_chars

    def _connect(self) -> sqlite3.Connection:
        conn = _connect_with_vec(self._path)
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

        await to_thread.run_sync(_write)

    async def remove_document(self, provider: str, identifier: str, format: str) -> None:
        """Remove every indexed vector for (provider, identifier, format). A no-op if none exist."""

        def _remove() -> None:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM document_vectors WHERE provider = ? AND identifier = ? AND format = ?",
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

        def _search() -> list[dict]:
            # Over-fetch before collapsing same-chunk_id sub-splits, so collapsing never starves
            # `limit` distinct chunks below what a caller asked for.
            fetch_k = limit * 4
            sql = (
                "SELECT provider, identifier, format, chunk_id, span_start, text, distance "
                "FROM document_vectors WHERE embedding MATCH ? AND k = ?"
            )
            params: list[object] = [json.dumps(query_embedding), fetch_k]
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
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            best_per_chunk: dict[tuple[str, str, str, str], sqlite3.Row] = {}
            for row in rows:
                key = (row["provider"], row["identifier"], row["format"], row["chunk_id"])
                existing = best_per_chunk.get(key)
                if existing is None or row["distance"] < existing["distance"]:
                    best_per_chunk[key] = row
            ordered = sorted(best_per_chunk.values(), key=lambda r: r["distance"])[:limit]
            return [
                {
                    "provider": row["provider"],
                    "identifier": row["identifier"],
                    "format": row["format"],
                    "chunk_id": row["chunk_id"],
                    "offset": row["span_start"],
                    "snippet": row["text"][:200] + ("..." if len(row["text"]) > 200 else ""),
                    "score": row["distance"],
                }
                for row in ordered
            ]

        return await to_thread.run_sync(_search)

    async def status(self, provider: str, identifier: str, format: str) -> IndexStatus:
        """Compare this document's recorded embedded_model against the currently configured one."""

        def _status() -> IndexStatus:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT embedded_model FROM document_vectors WHERE provider = ? AND identifier = ? AND format = ? LIMIT 1",
                    (provider, identifier, format),
                ).fetchone()
            if row is None:
                return "not_built"
            return "ready" if row["embedded_model"] == self._embedding_backend.model_name else "stale"

        return await to_thread.run_sync(_status)
