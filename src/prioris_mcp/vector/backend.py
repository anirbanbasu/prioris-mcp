"""Two corpus-wide VectorSearchBackend interfaces: documents and notes.

See docs/requirement-specification/search/02-vector-search.md#corpus-topology-two-corpus-wide-instances-not-one-unified-index-and-not-per-document.
Kept as separate ABCs, mirroring SearchIndex/NotesSearchIndex, rather than one generic
type-tagged interface - the two corpora have genuinely different filter/result shapes.
"""

from abc import ABC, abstractmethod
from typing import Literal

IndexStatus = Literal["not_built", "stale", "ready"]


class DocumentVectorSearchBackend(ABC):
    """Corpus-wide embedding index over every fetched document's chunks."""

    @abstractmethod
    async def index_entries(self, provider: str, identifier: str, format: str, entries: list[dict]) -> None:
        """Replace all indexed vectors for (provider, identifier, format) with `entries`.

        Each entry: {"chunk_id": str, "start": int, "length": int, "text": str}. A whole-document
        replace, delete-by-document-identity-then-insert - mirrors SearchIndex.index_entries.
        Embeds each entry's `text` internally via the injected EmbeddingBackend; callers pass raw
        text, never a pre-computed vector.
        """

    @abstractmethod
    async def remove_document(self, provider: str, identifier: str, format: str) -> None:
        """Remove every indexed vector for (provider, identifier, format). A no-op if none exist."""

    @abstractmethod
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

        Returns list of {"provider", "identifier", "format", "chunk_id", "offset", "snippet", "score"}.
        """

    @abstractmethod
    async def count(
        self, *, provider: str | None = None, identifier: str | None = None, format: str | None = None
    ) -> int:
        """Count of distinct indexed chunks matching the given filters — unthresholded, unpaged.

        Counts distinct (provider, identifier, format, chunk_id) — an oversized chunk's sub-splits
        (see `search()`) must not be double-counted relative to what `search()` actually returns.
        """

    @abstractmethod
    async def status(self, provider: str, identifier: str, format: str) -> IndexStatus:
        """Compare this document's recorded embedded_model against the currently configured one."""


class NoteVectorSearchBackend(ABC):
    """Corpus-wide embedding index over every note's text."""

    @abstractmethod
    async def index_note(self, note_id: str, text: str) -> None:
        """Replace this note's indexed vector (insert or reindex). Embeds `text` internally."""

    @abstractmethod
    async def remove_note(self, note_id: str) -> None:
        """Remove a note's indexed vector. A no-op if it isn't present."""

    @abstractmethod
    async def search(
        self, query_embedding: list[float], *, note_ids: list[str] | None = None, limit: int = 10
    ) -> list[dict]:
        """Cosine-similarity KNN search over notes, ranked most-similar first.

        `note_ids`, when given, scopes the search to that id set (e.g. notes on one document).
        Returns list of {"note_id", "score", "text_preview"}.
        """

    @abstractmethod
    async def status(self, note_id: str) -> IndexStatus:
        """Compare this note's recorded embedded_model against the currently configured one."""

    @abstractmethod
    async def has_any_indexed(self, model_name: str) -> bool:
        """Corpus-wide existence check: whether any note is indexed under `model_name`.

        Cheap existence-only check (no ranking/scoring) - lets a caller distinguish "index
        genuinely empty/never built" from "index built, this particular query just had zero
        matches" on an empty result page.
        """
