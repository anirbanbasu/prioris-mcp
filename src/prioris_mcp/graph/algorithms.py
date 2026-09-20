"""GraphAlgorithms: NetworkX-backed algorithms over a materialized subgraph.

See docs/requirement-specification/search/03-graph-search.md#graph-algorithm-backend-networkx and
#algorithm-surface-seven-fixed-methods-not-shortest_path-alone-and-not-the-full-networkx-catalog.
Not an ABC - one implementation works for any GraphSearchBackend, since it only ever calls
backend.subgraph() and then operates on the resulting plain dict, never anything engine-specific.
"""

import itertools

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

    async def communities(
        self, node_ids: list[NodeId], *, depth: int = 1, relation_type: str | None = None, seed: int | None = None
    ) -> list[list[NodeId]]:
        graph = await self._materialized_subgraph(node_ids, depth=depth, relation_type=relation_type)
        undirected = graph.to_undirected()
        # weight=None: same rationale as betweenness_centrality/pagerank above - every materialized
        # edge carries a `weight` attribute of `None` (from GraphEdge.weight) unless explicitly set,
        # and louvain_communities' default weight="weight" lookup sums those values, raising
        # `TypeError: unsupported operand type(s) for +: 'int' and 'NoneType'`.
        communities = nx.algorithms.community.louvain_communities(undirected, weight=None, seed=seed)
        return [sorted(community) for community in communities]

    async def paths(
        self, from_id: NodeId, to_id: NodeId, *, max_depth: int, max_paths: int = 20, relation_type: str | None = None
    ) -> list[list[dict]]:
        graph = await self._materialized_subgraph([from_id, to_id], depth=max_depth, relation_type=relation_type)
        if from_id not in graph or to_id not in graph:
            return []
        node_paths = list(nx.all_simple_paths(graph, from_id, to_id, cutoff=max_depth))
        node_paths.sort(key=len)
        hop_paths = []
        for node_path in node_paths[:max_paths]:
            hops = []
            for u, v in itertools.pairwise(node_path):
                edge_data = graph.get_edge_data(u, v)
                edge_attrs = next(iter(edge_data.values()))
                hops.append({"edge": dict(edge_attrs), "node": dict(graph.nodes[v])})
            hop_paths.append(hops)
        return hop_paths

    async def reachable(
        self, node_id: NodeId, *, direction: str = "out", max_depth: int, relation_type: str | None = None
    ) -> list[NodeId]:
        graph = await self._materialized_subgraph([node_id], depth=max_depth, relation_type=relation_type)
        if node_id not in graph:
            return []
        if direction == "out":
            return sorted(nx.descendants(graph, node_id))
        return sorted(nx.ancestors(graph, node_id))
