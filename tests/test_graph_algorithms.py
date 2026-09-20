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
