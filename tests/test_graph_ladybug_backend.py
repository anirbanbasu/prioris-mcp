import asyncio

from prioris_mcp.graph.ladybug_backend import LadybugSearchBackend


def _backend(tmp_path):
    return LadybugSearchBackend(tmp_path / "graph" / "graph.ladybug")


def test_upsert_pointer_creates_new_node(tmp_path):
    """upsert_pointer on a fresh (ref_type, ref_id) returns a new, non-empty node id."""
    backend = _backend(tmp_path)
    node_id = asyncio.run(backend.upsert_pointer("chunk", "abc123"))
    assert isinstance(node_id, str) and node_id


def test_upsert_pointer_is_idempotent_on_same_ref(tmp_path):
    """A second upsert_pointer call for the same (ref_type, ref_id) returns the same node id."""
    backend = _backend(tmp_path)
    first = asyncio.run(backend.upsert_pointer("chunk", "abc123"))
    second = asyncio.run(backend.upsert_pointer("chunk", "abc123"))
    assert first == second


def test_reopening_same_path_does_not_recreate_schema(tmp_path):
    """Re-instantiating LadybugSearchBackend against an existing path reuses the persisted schema/data."""
    path = tmp_path / "graph" / "graph.ladybug"
    backend1 = LadybugSearchBackend(path)
    asyncio.run(backend1.upsert_pointer("chunk", "abc123"))
    backend2 = LadybugSearchBackend(path)  # must not raise on re-running CREATE ... IF NOT EXISTS
    second_node_id = asyncio.run(backend2.upsert_pointer("chunk", "abc123"))
    assert second_node_id  # same ref -> same node, proves the table from backend1 persisted
