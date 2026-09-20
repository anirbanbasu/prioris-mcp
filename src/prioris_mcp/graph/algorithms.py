"""GraphAlgorithms: NetworkX-backed algorithms over a materialized subgraph.

See docs/requirement-specification/search/03-graph-search.md#graph-algorithm-backend-networkx and
#algorithm-surface-seven-fixed-methods-not-shortest_path-alone-and-not-the-full-networkx-catalog.
Not an ABC - one implementation works for any GraphSearchBackend, since it only ever calls
backend.subgraph() and then operates on the resulting plain dict, never anything engine-specific.
"""

import networkx as nx

from prioris_mcp.graph.backend import GraphSearchBackend, NodeId


class GraphAlgorithms:
    """NetworkX-backed algorithms over a materialized subgraph, independent of the storage engine."""

    def __init__(self, backend: GraphSearchBackend) -> None:
        self._backend = backend

    def materialize(self, nodes: list[dict], edges: list[dict]) -> nx.MultiDiGraph:
        """Build a directed multigraph from subgraph()/export_graph()'s {"nodes", "edges"} shape.

        Every node/edge dict field is carried over as a NetworkX attribute, so callers can
        reconstruct the original dict via `dict(graph.nodes[node_id])`/edge-data attrs without a
        second backend round trip.
        """
        graph = nx.MultiDiGraph()
        for node in nodes:
            graph.add_node(node["id"], **node)
        for edge in edges:
            graph.add_edge(edge["from_id"], edge["to_id"], key=edge["id"], **edge)
        return graph

    async def _materialized_subgraph(
        self, node_ids: list[NodeId], *, depth: int, relation_type: str | None
    ) -> nx.MultiDiGraph:
        raw = await self._backend.subgraph(node_ids, depth=depth, relation_type=relation_type)
        return self.materialize(raw["nodes"], raw["edges"])

    async def betweenness_centrality(
        self, node_ids: list[NodeId], *, depth: int = 1, relation_type: str | None = None
    ) -> dict[NodeId, float]:
        graph = await self._materialized_subgraph(node_ids, depth=depth, relation_type=relation_type)
        # weight=None: these two algorithms are unweighted per the requirement spec (only
        # steiner_tree uses edge weight). Without this, NetworkX's own weight="weight" default
        # would pick up the Edge model's `weight` attribute materialized onto each edge, which is
        # `None` unless explicitly set - breaking nx.pagerank's power-iteration convergence.
        return nx.betweenness_centrality(graph, weight=None)

    async def pagerank(
        self, node_ids: list[NodeId], *, depth: int = 1, relation_type: str | None = None
    ) -> dict[NodeId, float]:
        graph = await self._materialized_subgraph(node_ids, depth=depth, relation_type=relation_type)
        return nx.pagerank(graph, weight=None)
