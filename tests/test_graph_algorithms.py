import asyncio

from prioris_mcp.graph.algorithms import GraphAlgorithms
from prioris_mcp.graph.ladybug_backend import LadybugSearchBackend


def _algorithms(tmp_path):
    backend = LadybugSearchBackend(tmp_path / "graph" / "graph.ladybug")
    return backend, GraphAlgorithms(backend)


def test_materialize_builds_directed_multigraph_with_full_node_edge_attrs():
    """materialize() builds a directed multigraph carrying every node/edge dict field as an attr."""
    backend_pair = GraphAlgorithms.__new__(GraphAlgorithms)  # no backend needed for a pure function test
    nodes = [{"id": "a", "kind": "concept", "label": "a"}, {"id": "b", "kind": "concept", "label": "b"}]
    edges = [{"id": "e1", "from_id": "a", "to_id": "b", "relation_type": "related_to"}]
    graph = backend_pair.materialize(nodes, edges)
    assert graph.is_directed() and graph.is_multigraph()
    assert set(graph.nodes) == {"a", "b"}
    assert graph.nodes["a"]["label"] == "a"
    assert graph.has_edge("a", "b")


def test_betweenness_centrality_over_a_simple_chain(tmp_path):
    """Over a-b-c, the middle node b scores higher betweenness centrality than endpoint a."""
    backend, algorithms = _algorithms(tmp_path)
    a = asyncio.run(backend.create_concept("a"))
    b = asyncio.run(backend.create_concept("b"))
    c = asyncio.run(backend.create_concept("c"))
    asyncio.run(backend.create_edge(a, b, "related_to"))
    asyncio.run(backend.create_edge(b, c, "related_to"))
    scores = asyncio.run(algorithms.betweenness_centrality([a, b, c], depth=2))
    assert scores[b] > scores[a]


def test_pagerank_returns_a_score_per_node(tmp_path):
    """pagerank() returns a float score keyed by every requested node id."""
    backend, algorithms = _algorithms(tmp_path)
    a = asyncio.run(backend.create_concept("a"))
    b = asyncio.run(backend.create_concept("b"))
    asyncio.run(backend.create_edge(a, b, "related_to"))
    scores = asyncio.run(algorithms.pagerank([a, b], depth=1))
    assert set(scores.keys()) == {a, b}
    assert all(isinstance(v, float) for v in scores.values())


def test_communities_groups_a_disconnected_pair_and_a_triangle(tmp_path):
    """Louvain community detection separates a disconnected pair from a triangle."""
    backend, algorithms = _algorithms(tmp_path)
    a, b, c = (asyncio.run(backend.create_concept(name)) for name in ("a", "b", "c"))
    d, e = (asyncio.run(backend.create_concept(name)) for name in ("d", "e"))
    asyncio.run(backend.create_edge(a, b, "related_to"))
    asyncio.run(backend.create_edge(b, c, "related_to"))
    asyncio.run(backend.create_edge(a, c, "related_to"))
    asyncio.run(backend.create_edge(d, e, "related_to"))
    result = asyncio.run(algorithms.communities([a, b, c, d, e], depth=3, seed=0))
    assert len(result) >= 2
    flattened = {node_id for community in result for node_id in community}
    assert flattened == {a, b, c, d, e}


def test_paths_finds_and_sorts_all_simple_paths(tmp_path):
    """paths() returns every simple path, shortest first, ending on the target node."""
    backend, algorithms = _algorithms(tmp_path)
    a = asyncio.run(backend.create_concept("a"))
    b = asyncio.run(backend.create_concept("b"))
    c = asyncio.run(backend.create_concept("c"))
    asyncio.run(backend.create_edge(a, b, "related_to"))
    asyncio.run(backend.create_edge(b, c, "related_to"))
    asyncio.run(backend.create_edge(a, c, "related_to"))
    result = asyncio.run(algorithms.paths(a, c, max_depth=3))
    assert len(result) == 2
    assert len(result[0]) <= len(result[1])
    assert result[0][-1]["node"]["id"] == c


def test_paths_respects_max_paths_truncation(tmp_path):
    """paths() truncates the result to max_paths even when more simple paths exist."""
    backend, algorithms = _algorithms(tmp_path)
    a = asyncio.run(backend.create_concept("a"))
    c = asyncio.run(backend.create_concept("c"))
    for i in range(3):
        mid = asyncio.run(backend.create_concept(f"mid{i}"))
        asyncio.run(backend.create_edge(a, mid, "related_to"))
        asyncio.run(backend.create_edge(mid, c, "related_to"))
    result = asyncio.run(algorithms.paths(a, c, max_depth=2, max_paths=2))
    assert len(result) == 2


def test_reachable_descendants(tmp_path):
    """direction='out' (the default) walks nx.descendants."""
    backend, algorithms = _algorithms(tmp_path)
    a = asyncio.run(backend.create_concept("a"))
    b = asyncio.run(backend.create_concept("b"))
    c = asyncio.run(backend.create_concept("c"))
    asyncio.run(backend.create_edge(a, b, "related_to"))
    asyncio.run(backend.create_edge(b, c, "related_to"))
    result = asyncio.run(algorithms.reachable(a, direction="out", max_depth=2))
    assert set(result) == {b, c}


def test_reachable_ancestors(tmp_path):
    """direction='in' walks nx.ancestors instead of nx.descendants."""
    backend, algorithms = _algorithms(tmp_path)
    a = asyncio.run(backend.create_concept("a"))
    b = asyncio.run(backend.create_concept("b"))
    c = asyncio.run(backend.create_concept("c"))
    asyncio.run(backend.create_edge(a, b, "related_to"))
    asyncio.run(backend.create_edge(b, c, "related_to"))
    result = asyncio.run(algorithms.reachable(c, direction="in", max_depth=2))
    assert set(result) == {a, b}


class _EmptySubgraphBackend:
    """Stub backend whose subgraph() always reports nothing found.

    Exercises the "not in materialized subgraph" early returns that LadybugSearchBackend can't
    reach directly, since its own subgraph()/get_node() raises NotFoundError for unknown ids before
    GraphAlgorithms ever sees them. GraphAlgorithms is documented as backend-independent, so a
    different GraphSearchBackend implementation could plausibly omit unknown seed ids instead of
    raising.
    """

    async def subgraph(self, node_ids: list, *, depth: int, relation_type: str | None) -> dict:
        """Return an empty {"nodes", "edges"} shape regardless of the requested ids."""
        return {"nodes": [], "edges": []}


def test_paths_returns_empty_when_an_endpoint_is_missing_from_the_subgraph():
    """paths() short-circuits to [] when either endpoint is absent from the materialized subgraph."""
    algorithms = GraphAlgorithms(_EmptySubgraphBackend())
    result = asyncio.run(algorithms.paths("missing-a", "missing-b", max_depth=2))
    assert result == []


def test_reachable_returns_empty_when_the_node_is_missing_from_the_subgraph():
    """reachable() short-circuits to [] when the node is absent from the materialized subgraph."""
    algorithms = GraphAlgorithms(_EmptySubgraphBackend())
    result = asyncio.run(algorithms.reachable("missing", max_depth=2))
    assert result == []
