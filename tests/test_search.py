from __future__ import annotations
import pytest

from lethe.graph.search import cosine_similarity, doc_to_node


# --- cosine_similarity ---

def test_cosine_similarity_identical():
    v = [1.0, 0.0, 0.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector():
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_similarity_opposite():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(-1.0)


# --- doc_to_node strips vector_distance field ---

def test_doc_to_node_strips_vector_distance():
    data = {
        "node_type": "person",
        "content": "Jack",
        "domain": "entity",
        "weight": 0.55,
        "metadata": "{}",
        "entity_links": [],
        "user_id": "global",
        "vector_distance": 0.12,   # must be stripped, not cause validation error
    }
    node = doc_to_node("abc123", data)
    assert node.uuid == "abc123"
    assert node.content == "Jack"

