from __future__ import annotations
import math
import pytest

from lethe.graph.search import cosine_similarity, rrf_fuse
from lethe.models.node import Node


def _make_node(uid: str, weight: float = 0.5) -> Node:
    return Node(uuid=uid, node_type="generic", content=uid, weight=weight)


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


# --- rrf_fuse ---

def test_rrf_fuse_empty_inputs():
    assert rrf_fuse([], []) == []


def test_rrf_fuse_single_list():
    nodes = [_make_node("a"), _make_node("b")]
    result = rrf_fuse(nodes, [])
    assert [n.uuid for n in result] == ["a", "b"]


def test_rrf_fuse_deduplicates():
    n = _make_node("x")
    result = rrf_fuse([n], [n])
    assert len(result) == 1
    assert result[0].uuid == "x"


def test_rrf_fuse_ranks_overlap_higher():
    shared = _make_node("shared")
    only_vec = _make_node("only_vec")
    only_kw = _make_node("only_kw")

    result = rrf_fuse([shared, only_vec], [shared, only_kw])
    uuids = [n.uuid for n in result]
    # shared appears in both lists → higher RRF score → ranked first
    assert uuids[0] == "shared"


def test_rrf_fuse_preserves_all_nodes():
    vec = [_make_node(f"v{i}") for i in range(3)]
    kw = [_make_node(f"k{i}") for i in range(3)]
    result = rrf_fuse(vec, kw)
    assert len(result) == 6


def test_rrf_fuse_order_reflects_rank():
    # First in each list should score higher than last
    a = _make_node("first")
    b = _make_node("last")
    result = rrf_fuse([a, b], [])
    assert result[0].uuid == "first"
