"""GraphSearchBackend: a swappable-backend interface for PriorisMCP's knowledge graph.

Mirrors the same swappable-backend pattern as EmbeddingBackend/VectorSearchBackend - see
docs/requirement-specification/search/03-graph-search.md#graphsearchbackend-is-a-pluggable-interface.
No method here accepts a raw query string in any engine's own query language (ADR-00036) - every
capability is its own typed method.
"""

from abc import ABC, abstractmethod
from typing import Any, Literal

NodeId = str
EdgeId = str
RefType = Literal["chunk", "document", "note"]
MatchMode = Literal["starts_with", "contains", "ends_with"]
GraphExportFormat = Literal["cypher_json", "graphml"]


class GraphSearchBackend(ABC):
    """One corpus-wide knowledge graph over Pointer/Concept nodes and generic relationship edges."""

    @abstractmethod
    async def upsert_pointer(self, ref_type: RefType, ref_id: str, *, metadata: dict[str, Any] | None = None) -> NodeId:
        """Create or return the Pointer node for (ref_type, ref_id).

        metadata (if given) merges onto any existing Pointer's metadata - per-key, raising
        MetadataConflictError on a differing value for an existing key.
        """

    @abstractmethod
    async def create_concept(
        self,
        label: str,
        *,
        aliases: list[str] | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> NodeId:
        """Always creates a new Concept node - no upsert/dedup by label; call find_concepts first."""

    @abstractmethod
    async def update_concept(
        self,
        node_id: NodeId,
        *,
        label: str | None = None,
        aliases: list[str] | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Partial update: an unset (None) parameter leaves that field unchanged.

        metadata (if given) merges under the same per-key policy as upsert_pointer.
        Raises NotFoundError if node_id does not exist or is not a Concept.
        """

    @abstractmethod
    async def delete_node(self, node_id: NodeId) -> None:
        """Delete a node and cascade-delete every edge incident to it.

        Raises NotFoundError if node_id does not exist.
        """

    @abstractmethod
    async def create_edge(
        self,
        from_id: NodeId,
        to_id: NodeId,
        relation_type: str,
        *,
        weight: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EdgeId:
        """Create a new edge. Raises NotFoundError if either from_id or to_id does not exist."""

    @abstractmethod
    async def update_edge(
        self,
        edge_id: EdgeId,
        *,
        relation_type: str | None = None,
        weight: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Partial update, same semantics as update_concept. Raises NotFoundError if edge_id does not exist."""

    @abstractmethod
    async def delete_edge(self, edge_id: EdgeId) -> None:
        """Delete an edge. Raises NotFoundError if it does not exist."""

    @abstractmethod
    async def get_node(self, node_id: NodeId) -> dict:
        """Raises NotFoundError if node_id does not exist.

        Returns a dict carrying a computed "kind": "pointer" | "concept" key, synthesized at
        read time, never stored - plus that kind's own fields, id, metadata, created_at, updated_at.
        """

    @abstractmethod
    async def get_edge(self, edge_id: EdgeId) -> dict:
        """Raises NotFoundError if edge_id does not exist."""

    @abstractmethod
    async def neighbors(
        self,
        node_id: NodeId,
        *,
        direction: Literal["out", "in", "both"] = "both",
        relation_type: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        """Each result pairs the connecting edge with the neighboring node - {"edge": {...}, "node": {...}}."""

    @abstractmethod
    async def subgraph(self, node_ids: list[NodeId], *, depth: int = 1, relation_type: str | None = None) -> dict:
        """Every node/edge within `depth` hops of any given seed id, deduplicated.

        Returns {"nodes": [...], "edges": [...]}, optionally restricted to one relation_type.
        """

    @abstractmethod
    async def find_concepts(self, query: str, *, limit: int = 20) -> list[dict]:
        """Fuzzy-string-matched candidates (against every Concept's label+aliases), most-similar first."""

    @abstractmethod
    async def list_concepts(
        self,
        *,
        text: str | None = None,
        match: MatchMode = "contains",
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        """Unfiltered by default - browses every Concept node, paginated, newest first.

        `match` only takes effect when `text` is given, matching case-insensitively against
        `label` only - alias-matching is a deferred follow-up; `find_concepts`'s fuzzy matching
        already covers aliases (see above) for the caller-driven-dedup use case.
        """

    @abstractmethod
    async def export_graph(self) -> dict:
        """Every node and every edge in the graph, unfiltered.

        Returns the same shape as subgraph(), but with no seeds/depth bound.
        """
