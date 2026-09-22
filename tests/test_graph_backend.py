import pytest

from prioris_mcp.graph.backend import GraphSearchBackend


def test_graph_search_backend_cannot_be_instantiated_directly() -> None:
    """GraphSearchBackend is abstract and cannot be instantiated directly."""
    with pytest.raises(TypeError, match="abstract"):
        GraphSearchBackend()  # type: ignore  # pragma: no cover
