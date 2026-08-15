"""Search-mechanism registry: the seam `research_search_fetched`'s `mode` composes over.

See docs/requirement-specification/search/02-vector-search.md#composition-a-mode-parameter-not-a-new-opaque-smart-search.
Each mechanism is a thin adapter presenting a uniform search/status shape over a different
underlying backend (SearchIndex, DocumentVectorSearchBackend, and - in a later, independent
plan - GraphSearchBackend). `research_search_fetched` iterates whatever's registered; it never
hardcodes which mechanisms exist.
"""

from abc import ABC, abstractmethod

from prioris_mcp.notes.search_index import NotesSearchIndex
from prioris_mcp.storage.search_index import SearchIndex
from prioris_mcp.vector.backend import DocumentVectorSearchBackend, IndexStatus, NoteVectorSearchBackend
from prioris_mcp.vector.embedding import EmbeddingBackend
from prioris_mcp.vector.scheduler import EmbeddingScheduler


class SearchMechanism(ABC):
    """One retrieval mechanism registered for `research_search_fetched`'s `mode` parameter."""

    name: str

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        provider: str | None,
        identifier: str | None,
        format: str | None,
        offset: int = 0,
        limit: int,
    ) -> list[dict]:
        """Run this mechanism's search, returning its own result-shape dicts."""

    @abstractmethod
    async def count(self, query: str, *, provider: str | None, identifier: str | None, format: str | None) -> int:
        """Total matches for `query` and the given filters, ignoring paging."""

    @abstractmethod
    async def status(self, provider: str, identifier: str, format: str) -> IndexStatus:
        """This mechanism's index_status for one document."""


class FtsMechanism(SearchMechanism):
    """Adapts the existing `SearchIndex` (FTS5) to the `SearchMechanism` shape."""

    name = "fts"

    def __init__(self, search_index: SearchIndex) -> None:
        self._search_index = search_index

    async def search(
        self,
        query: str,
        *,
        provider: str | None,
        identifier: str | None,
        format: str | None,
        offset: int = 0,
        limit: int,
    ) -> list[dict]:
        return await self._search_index.search(
            query, provider=provider, identifier=identifier, format=format, offset=offset, limit=limit
        )

    async def count(self, query: str, *, provider: str | None, identifier: str | None, format: str | None) -> int:
        return await self._search_index.count(query, provider=provider, identifier=identifier, format=format)

    async def status(self, provider: str, identifier: str, format: str) -> IndexStatus:
        # FTS has no "model" to go stale against - existence-only, per
        # docs/requirement-specification/search/02-vector-search.md#search-responses-always-carry-per-mechanism-index_status-including-fts.
        exists = await self._search_index.has_entries(provider, identifier, format)
        return "ready" if exists else "not_built"


class VectorMechanism(SearchMechanism):
    """Adapts `DocumentVectorSearchBackend` + `EmbeddingBackend` to the `SearchMechanism` shape."""

    name = "vector"

    def __init__(
        self,
        vector_backend: DocumentVectorSearchBackend,
        embedding_backend: EmbeddingBackend,
        embedding_scheduler: EmbeddingScheduler,
    ) -> None:
        self._vector_backend = vector_backend
        self._embedding_backend = embedding_backend
        self._embedding_scheduler = embedding_scheduler

    async def search(
        self,
        query: str,
        *,
        provider: str | None,
        identifier: str | None,
        format: str | None,
        offset: int = 0,
        limit: int,
    ) -> list[dict]:
        query_embedding = await self._embedding_backend.embed(query)
        return await self._vector_backend.search(
            query_embedding, provider=provider, identifier=identifier, format=format, offset=offset, limit=limit
        )

    async def count(self, query: str, *, provider: str | None, identifier: str | None, format: str | None) -> int:
        return await self._vector_backend.count(provider=provider, identifier=identifier, format=format)

    async def status(self, provider: str, identifier: str, format: str) -> IndexStatus:
        """Backend's persisted status, overridden to `"building"` while a live re-embed task exists.

        `is_building` is checked before the persisted status because a live task can be rewriting
        this document's rows right now - the persisted status still reflects whatever the last
        completed write left behind (e.g. still `"ready"` under the old model mid-reindex), and
        `"building"` is the more actionable signal for a caller deciding whether to poll again.
        """
        if self._embedding_scheduler.is_building((provider, identifier, format)):
            return "building"
        return await self._vector_backend.status(provider, identifier, format)


class NotesFtsMechanism:
    """Adapts `NotesSearchIndex` to a notes-scoped search shape.

    Not a `SearchMechanism` subclass: notes-search's structural filters (provider, date range,
    tags, author, ...) live on `NotesBackend.search` itself, applied by the caller before this
    mechanism ever runs - so `search` here takes only `query`/`limit`, no provider/identifier/
    format scoping and no `status` method.
    """

    name = "fts"

    def __init__(self, notes_search_index: NotesSearchIndex) -> None:
        self._notes_search_index = notes_search_index

    async def search(self, query: str, *, limit: int) -> list[str]:
        return (await self._notes_search_index.search(query))[:limit]


class NotesVectorMechanism:
    """Adapts `NoteVectorSearchBackend` + `EmbeddingBackend` to a notes-scoped search shape."""

    name = "vector"

    def __init__(self, note_vector_backend: NoteVectorSearchBackend, embedding_backend: EmbeddingBackend) -> None:
        self._note_vector_backend = note_vector_backend
        self._embedding_backend = embedding_backend

    async def search(
        self, query: str, *, note_ids: list[str] | None = None, offset: int = 0, limit: int = 10
    ) -> list[dict]:
        query_embedding = await self._embedding_backend.embed(query)
        return await self._note_vector_backend.search(query_embedding, note_ids=note_ids, offset=offset, limit=limit)

    async def count(self, *, note_ids: list[str] | None = None) -> int:
        return await self._note_vector_backend.count(note_ids=note_ids)
