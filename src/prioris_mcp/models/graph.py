"""Pydantic models for GraphSearchBackend/GraphAlgorithms results - the MCP wire boundary for graph search.

See docs/requirement-specification/search/03-graph-search.md#tool-grouping-three-op-dispatched-tools-not-one-tool-per-operation-or-one-mega-tool.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from prioris_mcp.graph.backend import EdgeId, NodeId, RefType


class PointerNode(BaseModel):
    """A graph node linking to something that already has a home elsewhere in PriorisMCP."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["pointer"]
    id: NodeId
    ref_type: RefType
    ref_id: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class ConceptNode(BaseModel):
    """A graph node owning its own label/aliases/description."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["concept"]
    id: NodeId
    label: str
    aliases: list[str]
    description: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


GraphNode = Annotated[PointerNode | ConceptNode, Field(discriminator="kind")]


class GraphEdge(BaseModel):
    """One edge on the generic Related relationship table."""

    model_config = ConfigDict(extra="forbid")

    id: EdgeId
    from_id: NodeId
    to_id: NodeId
    relation_type: str
    weight: float | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class GraphHop(BaseModel):
    """One connecting edge paired with the neighboring/reached node."""

    model_config = ConfigDict(extra="forbid")

    edge: GraphEdge
    node: GraphNode


class ConceptMatch(BaseModel):
    """One find_concepts candidate, with its fuzzy-match score."""

    model_config = ConfigDict(extra="forbid")

    node: ConceptNode
    score: float


class GraphWriteResult(BaseModel):
    """Result of any research_graph_write operation - a single created/updated/deleted id."""

    model_config = ConfigDict(extra="forbid")

    op: Literal[
        "upsert_pointer",
        "create_concept",
        "update_concept",
        "delete_node",
        "create_edge",
        "update_edge",
        "delete_edge",
    ]
    id: str


class NodeResult(BaseModel):
    """Result of research_graph_query op="get_node"."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["get_node"]
    node: GraphNode


class EdgeResult(BaseModel):
    """Result of research_graph_query op="get_edge"."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["get_edge"]
    edge: GraphEdge


class NeighborsResult(BaseModel):
    """Result of research_graph_query op="neighbors"."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["neighbors"]
    matches: list[GraphHop]


class ConceptMatchesResult(BaseModel):
    """Result of research_graph_query op="find_concepts"."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["find_concepts"]
    matches: list[ConceptMatch]


class SubgraphResult(BaseModel):
    """Result of research_graph_query op="subgraph"."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["subgraph"]
    nodes: list[GraphNode]
    edges: list[GraphEdge]


GraphQueryResult = Annotated[
    NodeResult | EdgeResult | NeighborsResult | ConceptMatchesResult | SubgraphResult,
    Field(discriminator="op"),
]


class CentralityResult(BaseModel):
    """Result of research_graph_analyze op="betweenness_centrality"|"pagerank"."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["betweenness_centrality", "pagerank"]
    scores: dict[NodeId, float]


class CommunitiesResult(BaseModel):
    """Result of research_graph_analyze op="communities"."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["communities"]
    communities: list[list[NodeId]]


class PathsResult(BaseModel):
    """Result of research_graph_analyze op="paths"."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["paths"]
    paths: list[list[GraphHop]]


class ReachableResult(BaseModel):
    """Result of research_graph_analyze op="reachable"."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["reachable"]
    node_ids: list[NodeId]


class SteinerTreeResult(BaseModel):
    """Result of research_graph_analyze op="steiner_tree"."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["steiner_tree"]
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class PredictedLink(BaseModel):
    """One candidate edge suggested by predict_links, for a human/LLM to review."""

    model_config = ConfigDict(extra="forbid")

    from_id: NodeId
    to_id: NodeId
    score: float


class PredictedLinksResult(BaseModel):
    """Result of research_graph_analyze op="predict_links"."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["predict_links"]
    predictions: list[PredictedLink]


GraphAnalyzeResult = Annotated[
    CentralityResult | CommunitiesResult | PathsResult | ReachableResult | SteinerTreeResult | PredictedLinksResult,
    Field(discriminator="op"),
]
