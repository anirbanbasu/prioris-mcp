"""Search-mechanism registry: the seam `research_search_fetched`'s `mode` composes over.

See docs/requirement-specification/search/02-vector-search.md#composition-a-mode-parameter-not-a-new-opaque-smart-search.
Each mechanism is a thin adapter presenting a uniform search/status shape over a different
underlying backend (SearchIndex, DocumentVectorSearchBackend, and - in a later, independent
plan - GraphSearchBackend). `research_search_fetched` iterates whatever's registered; it never
hardcodes which mechanisms exist.
"""

from abc import ABC, abstractmethod

from prioris_mcp.storage.search_index import SearchIndex
from prioris_mcp.vector.backend import DocumentVectorSearchBackend, IndexStatus
from prioris_mcp.vector.embedding import EmbeddingBackend


class SearchMechanism(ABC):
    """One retrieval mechanism registered for `research_search_fetched`'s `mode` parameter."""

    name: str

    @abstractmethod
    async def search(
        self, query: str, *, provider: str | None, identifier: str | None, format: str | None, limit: int
    ) -> list[dict]:
        """Run this mechanism's search, returning its own result-shape dicts."""

    @abstractmethod
    async def status(self, provider: str, identifier: str, format: str) -> IndexStatus:
        """This mechanism's index_status for one document."""


class FtsMechanism(SearchMechanism):
    """Adapts the existing `SearchIndex` (FTS5) to the `SearchMechanism` shape."""

    name = "fts"

    def __init__(self, search_index: SearchIndex) -> None:
        self._search_index = search_index

    async def search(
        self, query: str, *, provider: str | None, identifier: str | None, format: str | None, limit: int
    ) -> list[dict]:
        results = await self._search_index.search(query, provider=provider, identifier=identifier, format=format)
        return results[:limit]

    async def status(self, provider: str, identifier: str, format: str) -> IndexStatus:
        # FTS has no "model" to go stale against - existence-only, per
        # docs/requirement-specification/search/02-vector-search.md#search-responses-always-carry-per-mechanism-index_status-including-fts.
        exists = await self._search_index.has_entries(provider, identifier, format)
        return "ready" if exists else "not_built"


class VectorMechanism(SearchMechanism):
    """Adapts `DocumentVectorSearchBackend` + `EmbeddingBackend` to the `SearchMechanism` shape."""

    name = "vector"

    def __init__(self, vector_backend: DocumentVectorSearchBackend, embedding_backend: EmbeddingBackend) -> None:
        self._vector_backend = vector_backend
        self._embedding_backend = embedding_backend

    async def search(
        self, query: str, *, provider: str | None, identifier: str | None, format: str | None, limit: int
    ) -> list[dict]:
        query_embedding = await self._embedding_backend.embed(query)
        return await self._vector_backend.search(
            query_embedding, provider=provider, identifier=identifier, format=format, limit=limit
        )

    async def status(self, provider: str, identifier: str, format: str) -> IndexStatus:
        return await self._vector_backend.status(provider, identifier, format)
