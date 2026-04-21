from __future__ import annotations

from lethe.graph.serialization import doc_to_edge, doc_to_node


def test_doc_to_node_basic():
    node = doc_to_node("abc123", {"node_type": "person", "content": "Alice"})
    assert node.uuid == "abc123"
    assert node.node_type == "person"
    assert node.content == "Alice"


def test_doc_to_node_strips_vector_distance():
    data = {"node_type": "person", "content": "Bob", "vector_distance": 0.9}
    node = doc_to_node("x", data)
    assert not hasattr(node, "vector_distance")
    assert "vector_distance" not in data


def test_doc_to_edge_basic():
    edge = doc_to_edge(
        "rel1",
        {"subject_uuid": "a", "predicate": "knows", "object_uuid": "b"},
    )
    assert edge.uuid == "rel1"
    assert edge.predicate == "knows"


def test_doc_to_edge_strips_vector_distance():
    data = {"subject_uuid": "a", "predicate": "p", "object_uuid": "b", "vector_distance": 0.1}
    doc_to_edge("r", data)
    assert "vector_distance" not in data
