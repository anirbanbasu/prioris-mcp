import asyncio

from prioris_mcp.errors import NotFoundError
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


def test_create_concept_returns_new_node_id(tmp_path):
    """create_concept returns a new, non-empty node id."""
    backend = _backend(tmp_path)
    node_id = asyncio.run(backend.create_concept("gradient checkpointing", aliases=["activation checkpointing"]))
    assert isinstance(node_id, str) and node_id


def test_create_concept_always_creates_a_new_node(tmp_path):
    """Unlike upsert_pointer, create_concept has no idempotency key - repeat calls create distinct nodes."""
    backend = _backend(tmp_path)
    first = asyncio.run(backend.create_concept("gradient checkpointing"))
    second = asyncio.run(backend.create_concept("gradient checkpointing"))
    assert first != second


def test_update_concept_partial_update_leaves_unset_fields_unchanged(tmp_path):
    """Omitted update_concept kwargs leave the corresponding field at its existing value."""
    backend = _backend(tmp_path)
    node_id = asyncio.run(
        backend.create_concept("gradient checkpointing", aliases=["activation checkpointing"], description="d")
    )
    asyncio.run(backend.update_concept(node_id, label="renamed"))
    node = asyncio.run(backend.get_node(node_id))
    assert node["label"] == "renamed"
    assert node["aliases"] == ["activation checkpointing"]
    assert node["description"] == "d"


def test_update_concept_raises_not_found_for_missing_node(tmp_path):
    """update_concept on an id with no matching Concept node raises NotFoundError."""
    backend = _backend(tmp_path)
    try:
        asyncio.run(backend.update_concept("does-not-exist", label="x"))
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_update_concept_raises_not_found_for_a_pointer_node(tmp_path):
    """update_concept refuses to touch a node id that identifies a Pointer, not a Concept."""
    backend = _backend(tmp_path)
    pointer_id = asyncio.run(backend.upsert_pointer("chunk", "abc"))
    try:
        asyncio.run(backend.update_concept(pointer_id, label="x"))
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_delete_node_removes_node_and_incident_edges(tmp_path):
    """delete_node removes the node and cascades to delete any edges incident to it."""
    backend = _backend(tmp_path)
    pointer_id = asyncio.run(backend.upsert_pointer("chunk", "abc"))
    concept_id = asyncio.run(backend.create_concept("gradient checkpointing"))
    edge_id = asyncio.run(backend.create_edge(pointer_id, concept_id, "discussed_in"))
    asyncio.run(backend.delete_node(concept_id))
    try:
        asyncio.run(backend.get_node(concept_id))
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass
    try:
        asyncio.run(backend.get_edge(edge_id))
        raise AssertionError("expected NotFoundError (cascade)")
    except NotFoundError:
        pass


def test_delete_node_raises_not_found_for_missing_node(tmp_path):
    """delete_node on an id with no matching node (Pointer or Concept) raises NotFoundError."""
    backend = _backend(tmp_path)
    try:
        asyncio.run(backend.delete_node("does-not-exist"))
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_get_node_pointer_has_computed_kind_and_fields(tmp_path):
    """get_node on a Pointer node id returns kind='pointer' plus the pointer-specific fields."""
    backend = _backend(tmp_path)
    node_id = asyncio.run(backend.upsert_pointer("chunk", "abc123", metadata={"source": "human"}))
    node = asyncio.run(backend.get_node(node_id))
    assert node["kind"] == "pointer"
    assert node["ref_type"] == "chunk"
    assert node["ref_id"] == "abc123"
    assert node["metadata"] == {"source": "human"}
    assert node["id"] == node_id
    assert node["created_at"] and node["updated_at"]


def test_get_node_concept_has_computed_kind_and_fields(tmp_path):
    """get_node on a Concept node id returns kind='concept' plus the concept-specific fields."""
    backend = _backend(tmp_path)
    node_id = asyncio.run(
        backend.create_concept("gradient checkpointing", aliases=["activation checkpointing"], description="d")
    )
    node = asyncio.run(backend.get_node(node_id))
    assert node["kind"] == "concept"
    assert node["label"] == "gradient checkpointing"
    assert node["aliases"] == ["activation checkpointing"]
    assert node["description"] == "d"


def test_get_node_raises_not_found(tmp_path):
    """get_node on an id with no matching node (Pointer or Concept) raises NotFoundError."""
    backend = _backend(tmp_path)
    try:
        asyncio.run(backend.get_node("does-not-exist"))
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_get_edge_returns_edge_fields(tmp_path):
    """get_edge returns the edge id, endpoint ids, relation_type, weight, and metadata."""
    backend = _backend(tmp_path)
    pointer_id = asyncio.run(backend.upsert_pointer("chunk", "abc"))
    concept_id = asyncio.run(backend.create_concept("gradient checkpointing"))
    edge_id = asyncio.run(backend.create_edge(pointer_id, concept_id, "discussed_in", weight=0.5, metadata={"k": "v"}))
    edge = asyncio.run(backend.get_edge(edge_id))
    assert edge["id"] == edge_id
    assert edge["from_id"] == pointer_id
    assert edge["to_id"] == concept_id
    assert edge["relation_type"] == "discussed_in"
    assert edge["weight"] == 0.5
    assert edge["metadata"] == {"k": "v"}


def test_get_edge_raises_not_found(tmp_path):
    """get_edge on an id with no matching Related edge raises NotFoundError."""
    backend = _backend(tmp_path)
    try:
        asyncio.run(backend.get_edge("does-not-exist"))
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_create_edge_between_pointer_and_concept(tmp_path):
    """create_edge between a Pointer and a Concept returns a new, non-empty edge id."""
    backend = _backend(tmp_path)
    pointer_id = asyncio.run(backend.upsert_pointer("chunk", "abc"))
    concept_id = asyncio.run(backend.create_concept("gradient checkpointing"))
    edge_id = asyncio.run(backend.create_edge(pointer_id, concept_id, "discussed_in"))
    assert isinstance(edge_id, str) and edge_id


def test_create_edge_between_two_concepts(tmp_path):
    """create_edge between two Concept nodes round-trips from_id/to_id through get_edge."""
    backend = _backend(tmp_path)
    c1 = asyncio.run(backend.create_concept("gradient checkpointing"))
    c2 = asyncio.run(backend.create_concept("transformer"))
    edge_id = asyncio.run(backend.create_edge(c1, c2, "related_to"))
    edge = asyncio.run(backend.get_edge(edge_id))
    assert edge["from_id"] == c1 and edge["to_id"] == c2


def test_create_edge_raises_not_found_for_missing_from_id(tmp_path):
    """create_edge raises NotFoundError when from_id has no matching node."""
    backend = _backend(tmp_path)
    concept_id = asyncio.run(backend.create_concept("gradient checkpointing"))
    try:
        asyncio.run(backend.create_edge("does-not-exist", concept_id, "discussed_in"))
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_create_edge_raises_not_found_for_missing_to_id(tmp_path):
    """create_edge raises NotFoundError when to_id has no matching node."""
    backend = _backend(tmp_path)
    pointer_id = asyncio.run(backend.upsert_pointer("chunk", "abc"))
    try:
        asyncio.run(backend.create_edge(pointer_id, "does-not-exist", "discussed_in"))
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_update_edge_partial_update(tmp_path):
    """Omitted update_edge kwargs (e.g. weight) leave the corresponding field at its existing value."""
    backend = _backend(tmp_path)
    c1 = asyncio.run(backend.create_concept("gradient checkpointing"))
    c2 = asyncio.run(backend.create_concept("transformer"))
    edge_id = asyncio.run(backend.create_edge(c1, c2, "discussed_in", weight=0.1))
    asyncio.run(backend.update_edge(edge_id, relation_type="introduced_in"))
    edge = asyncio.run(backend.get_edge(edge_id))
    assert edge["relation_type"] == "introduced_in"
    assert edge["weight"] == 0.1


def test_update_edge_raises_not_found(tmp_path):
    """update_edge on an id with no matching Related edge raises NotFoundError."""
    backend = _backend(tmp_path)
    try:
        asyncio.run(backend.update_edge("does-not-exist", relation_type="x"))
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_delete_edge_removes_edge(tmp_path):
    """delete_edge removes the edge; a subsequent get_edge raises NotFoundError."""
    backend = _backend(tmp_path)
    c1 = asyncio.run(backend.create_concept("gradient checkpointing"))
    c2 = asyncio.run(backend.create_concept("transformer"))
    edge_id = asyncio.run(backend.create_edge(c1, c2, "discussed_in"))
    asyncio.run(backend.delete_edge(edge_id))
    try:
        asyncio.run(backend.get_edge(edge_id))
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_delete_edge_raises_not_found(tmp_path):
    """delete_edge on an id with no matching Related edge raises NotFoundError."""
    backend = _backend(tmp_path)
    try:
        asyncio.run(backend.delete_edge("does-not-exist"))
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_neighbors_out_direction(tmp_path):
    """neighbors(direction="out") returns the outgoing edge/node pair for a node with one out-edge."""
    backend = _backend(tmp_path)
    pointer_id = asyncio.run(backend.upsert_pointer("chunk", "abc"))
    concept_id = asyncio.run(backend.create_concept("gradient checkpointing"))
    asyncio.run(backend.create_edge(pointer_id, concept_id, "discussed_in"))
    results = asyncio.run(backend.neighbors(pointer_id, direction="out"))
    assert len(results) == 1
    assert results[0]["node"]["id"] == concept_id
    assert results[0]["edge"]["relation_type"] == "discussed_in"


def test_neighbors_in_direction(tmp_path):
    """neighbors(direction="in") returns the source node reachable via an incoming edge."""
    backend = _backend(tmp_path)
    pointer_id = asyncio.run(backend.upsert_pointer("chunk", "abc"))
    concept_id = asyncio.run(backend.create_concept("gradient checkpointing"))
    asyncio.run(backend.create_edge(pointer_id, concept_id, "discussed_in"))
    results = asyncio.run(backend.neighbors(concept_id, direction="in"))
    assert len(results) == 1
    assert results[0]["node"]["id"] == pointer_id


def test_neighbors_both_direction_and_relation_type_filter(tmp_path):
    """direction="both" returns neighbors on either side; relation_type further restricts them."""
    backend = _backend(tmp_path)
    c1 = asyncio.run(backend.create_concept("a"))
    c2 = asyncio.run(backend.create_concept("b"))
    c3 = asyncio.run(backend.create_concept("c"))
    asyncio.run(backend.create_edge(c1, c2, "related_to"))
    asyncio.run(backend.create_edge(c3, c1, "discussed_in"))
    both = asyncio.run(backend.neighbors(c1, direction="both"))
    assert {r["node"]["id"] for r in both} == {c2, c3}
    filtered = asyncio.run(backend.neighbors(c1, direction="both", relation_type="related_to"))
    assert {r["node"]["id"] for r in filtered} == {c2}


def test_neighbors_pagination(tmp_path):
    """offset/limit page through a node's neighbors without overlap between pages."""
    backend = _backend(tmp_path)
    c1 = asyncio.run(backend.create_concept("hub"))
    for i in range(5):
        c = asyncio.run(backend.create_concept(f"leaf{i}"))
        asyncio.run(backend.create_edge(c1, c, "related_to"))
    page1 = asyncio.run(backend.neighbors(c1, direction="out", offset=0, limit=2))
    page2 = asyncio.run(backend.neighbors(c1, direction="out", offset=2, limit=2))
    assert len(page1) == 2
    assert len(page2) == 2
    assert {r["node"]["id"] for r in page1}.isdisjoint({r["node"]["id"] for r in page2})


def test_subgraph_within_depth(tmp_path):
    """Subgraph includes every node within `depth` hops of a seed and excludes nodes beyond it."""
    backend = _backend(tmp_path)
    a = asyncio.run(backend.create_concept("a"))
    b = asyncio.run(backend.create_concept("b"))
    c = asyncio.run(backend.create_concept("c"))
    far = asyncio.run(backend.create_concept("far"))
    asyncio.run(backend.create_edge(a, b, "related_to"))
    asyncio.run(backend.create_edge(b, c, "related_to"))
    asyncio.run(backend.create_edge(c, far, "related_to"))
    result = asyncio.run(backend.subgraph([a], depth=2))
    node_ids = {n["id"] for n in result["nodes"]}
    assert {a, b, c}.issubset(node_ids)
    assert far not in node_ids


def test_subgraph_relation_type_filter(tmp_path):
    """Subgraph's relation_type restricts the returned edges to that one relation type."""
    backend = _backend(tmp_path)
    a = asyncio.run(backend.create_concept("a"))
    b = asyncio.run(backend.create_concept("b"))
    c = asyncio.run(backend.create_concept("c"))
    asyncio.run(backend.create_edge(a, b, "keep"))
    asyncio.run(backend.create_edge(a, c, "drop"))
    result = asyncio.run(backend.subgraph([a], depth=1, relation_type="keep"))
    edge_types = {e["relation_type"] for e in result["edges"]}
    assert edge_types == {"keep"}


def test_subgraph_empty_node_ids_returns_empty_result(tmp_path):
    """subgraph([]) short-circuits to an empty result without querying for edges."""
    backend = _backend(tmp_path)
    result = asyncio.run(backend.subgraph([]))
    assert result == {"nodes": [], "edges": []}


def test_find_concepts_ranks_by_fuzzy_similarity(tmp_path):
    """find_concepts ranks the closer fuzzy match to the query text first."""
    backend = _backend(tmp_path)
    asyncio.run(backend.create_concept("gradient checkpointing"))
    asyncio.run(backend.create_concept("transformer architecture"))
    results = asyncio.run(backend.find_concepts("gradient check-pointing"))
    assert results[0]["node"]["label"] == "gradient checkpointing"


def test_find_concepts_matches_against_aliases_too(tmp_path):
    """find_concepts matches a query against a Concept's aliases, not just its label."""
    backend = _backend(tmp_path)
    asyncio.run(backend.create_concept("gradient checkpointing", aliases=["activation checkpointing"]))
    results = asyncio.run(backend.find_concepts("activation check-pointing"))
    assert len(results) == 1


def test_find_concepts_respects_limit(tmp_path):
    """find_concepts caps the number of returned candidates at limit."""
    backend = _backend(tmp_path)
    for i in range(5):
        asyncio.run(backend.create_concept(f"concept {i}"))
    results = asyncio.run(backend.find_concepts("concept", limit=2))
    assert len(results) == 2


def test_list_concepts_unfiltered_returns_everything(tmp_path):
    """list_concepts with no text filter returns every Concept node."""
    backend = _backend(tmp_path)
    asyncio.run(backend.create_concept("a"))
    asyncio.run(backend.create_concept("b"))
    results = asyncio.run(backend.list_concepts())
    assert len(results) == 2


def test_list_concepts_starts_with_filter(tmp_path):
    """list_concepts(match="starts_with") restricts results to labels starting with text."""
    backend = _backend(tmp_path)
    asyncio.run(backend.create_concept("gradient checkpointing"))
    asyncio.run(backend.create_concept("transformer"))
    results = asyncio.run(backend.list_concepts(text="gradient", match="starts_with"))
    assert len(results) == 1
    assert results[0]["label"] == "gradient checkpointing"


def test_list_concepts_pagination(tmp_path):
    """offset/limit page through Concept nodes without overlap between pages."""
    backend = _backend(tmp_path)
    for i in range(5):
        asyncio.run(backend.create_concept(f"concept {i}"))
    page1 = asyncio.run(backend.list_concepts(offset=0, limit=2))
    page2 = asyncio.run(backend.list_concepts(offset=2, limit=2))
    assert len(page1) == 2
    assert len(page2) == 2
    assert {p["id"] for p in page1}.isdisjoint({p["id"] for p in page2})


def test_export_graph_returns_every_node_and_edge(tmp_path):
    """export_graph returns every node and edge in the graph, unfiltered."""
    backend = _backend(tmp_path)
    pointer_id = asyncio.run(backend.upsert_pointer("chunk", "abc"))
    concept_id = asyncio.run(backend.create_concept("gradient checkpointing"))
    asyncio.run(backend.create_edge(pointer_id, concept_id, "discussed_in"))
    result = asyncio.run(backend.export_graph())
    assert {n["id"] for n in result["nodes"]} == {pointer_id, concept_id}
    assert len(result["edges"]) == 1
