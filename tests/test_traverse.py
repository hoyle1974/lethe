from __future__ import annotations
import pytest

from lethe.graph.traverse import prune_frontier_by_similarity
from lethe.models.node import Node


def _node(uuid: str, emb: list[float]) -> Node:
    n = Node(uuid=uuid, node_type="generic", content=uuid)
    n.embedding = emb
    return n


def test_prune_frontier_keeps_top_k():
    query = [1.0, 0.0]
    nodes = [
        _node("a", [1.0, 0.0]),   # similarity 1.0 — closest
        _node("b", [0.7, 0.7]),   # similarity ~0.7
        _node("c", [0.0, 1.0]),   # similarity 0.0 — furthest
    ]
    pruned = prune_frontier_by_similarity(nodes, query, top_k=2)
    uuids = [n.uuid for n in pruned]
    assert "a" in uuids
    assert "b" in uuids
    assert "c" not in uuids


def test_prune_frontier_no_query_applies_hard_cap():
    nodes = [_node(str(i), [float(i), 0.0]) for i in range(10)]
    pruned = prune_frontier_by_similarity(nodes, None, top_k=3)
    assert len(pruned) == 3


def test_prune_frontier_fewer_than_k():
    nodes = [_node("a", [1.0, 0.0])]
    pruned = prune_frontier_by_similarity(nodes, [1.0, 0.0], top_k=5)
    assert len(pruned) == 1
