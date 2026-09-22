from pydantic import TypeAdapter

from prioris_mcp.models.graph import (
    ConceptMatch,
    ConceptMatchesResult,
    ConceptNode,
    GraphEdge,
    GraphHop,
    GraphNode,
    GraphQueryResult,
    GraphWriteResult,
    NodeResult,
    PointerNode,
)


def test_pointer_node_round_trips():
    """PointerNode serializes and deserializes correctly."""
    node = PointerNode(
        kind="pointer",
        id="n1",
        ref_type="chunk",
        ref_id="abc",
        metadata={"k": "v"},
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    assert node.model_validate_json(node.model_dump_json()) == node


def test_graph_node_discriminates_on_kind():
    """GraphNode discriminator correctly identifies ConceptNode from kind field."""
    adapter = TypeAdapter(GraphNode)
    concept = adapter.validate_python(
        {
            "kind": "concept",
            "id": "n2",
            "label": "gradient checkpointing",
            "aliases": [],
            "description": None,
            "metadata": {},
            "created_at": "t",
            "updated_at": "t",
        }
    )
    assert isinstance(concept, ConceptNode)


def test_graph_write_result_carries_op_and_id():
    """GraphWriteResult carries op and id fields."""
    result = GraphWriteResult(op="create_concept", id="n1")
    assert result.op == "create_concept"


def test_graph_query_result_discriminates_on_op():
    """GraphQueryResult discriminator correctly identifies NodeResult from op field."""
    adapter = TypeAdapter(GraphQueryResult)
    node_result = adapter.validate_python(
        {
            "op": "get_node",
            "node": {
                "kind": "pointer",
                "id": "n1",
                "ref_type": "chunk",
                "ref_id": "abc",
                "metadata": {},
                "created_at": "t",
                "updated_at": "t",
            },
        }
    )
    assert isinstance(node_result, NodeResult)


def test_concept_matches_result_holds_scored_matches():
    """ConceptMatchesResult holds scored matches."""
    result = ConceptMatchesResult(
        op="find_concepts",
        matches=[
            ConceptMatch(
                node=ConceptNode(
                    kind="concept",
                    id="c1",
                    label="gradient checkpointing",
                    aliases=[],
                    description=None,
                    metadata={},
                    created_at="t",
                    updated_at="t",
                ),
                score=95.0,
            )
        ],
    )
    assert result.matches[0].score == 95.0


def test_graph_hop_pairs_edge_and_node():
    """GraphHop pairs an edge with a node."""
    hop = GraphHop(
        edge=GraphEdge(
            id="e1",
            from_id="n1",
            to_id="n2",
            relation_type="discussed_in",
            weight=None,
            metadata={},
            created_at="t",
            updated_at="t",
        ),
        node=PointerNode(
            kind="pointer", id="n2", ref_type="chunk", ref_id="abc", metadata={}, created_at="t", updated_at="t"
        ),
    )
    assert hop.edge.relation_type == "discussed_in"
