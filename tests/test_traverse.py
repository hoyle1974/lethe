from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from lethe.graph.traverse import (
    _fetch_nodes_by_ids,
    _is_alive,
    apply_self_seed_neighbor_floor,
    prune_frontier_by_similarity,
)
from lethe.models.node import Node


def _config():
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test"}, clear=True):
        from lethe.config import Config

        return Config(_env_file=None)


def _node(uuid: str, emb: list[float]) -> Node:
    n = Node(uuid=uuid, node_type="generic", content=uuid)
    n.embedding = emb
    return n


def test_prune_frontier_keeps_top_k():
    query = [1.0, 0.0]
    nodes = [
        _node("a", [1.0, 0.0]),  # similarity 1.0 — closest
        _node("b", [0.7, 0.7]),  # similarity ~0.7
        _node("c", [0.0, 1.0]),  # similarity 0.0 — furthest
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


def test_is_alive_excludes_tombstones():
    assert not _is_alive(Node(uuid="t", node_type="relationship", content="x", weight=0.0))
    assert _is_alive(Node(uuid="a", node_type="relationship", content="x", weight=0.01))


def test_prune_frontier_fewer_than_k():
    nodes = [_node("a", [1.0, 0.0])]
    pruned = prune_frontier_by_similarity(nodes, [1.0, 0.0], top_k=5)
    assert len(pruned) == 1


def test_prune_frontier_weighted_score_prioritizes_observation_density():
    query = [1.0, 0.0]
    semantically_best = _node("sem", [1.0, 0.0])
    semantically_best.journal_entry_ids = []
    reinforced = _node("reinforced", [0.6, 0.8])  # cosine similarity 0.6
    reinforced.journal_entry_ids = ["j1", "j2", "j3", "j4", "j5"]
    pruned = prune_frontier_by_similarity([semantically_best, reinforced], query, top_k=1)
    assert [n.uuid for n in pruned] == ["reinforced"]


def test_apply_self_seed_neighbor_floor_expands_first_hop_from_self():
    pruned = [_node("keep-1", [1.0, 0.0])]
    self_neighbors = [
        _node("self-rel-1", [0.9, 0.1]),
        _node("self-rel-2", [0.8, 0.2]),
    ]
    result = apply_self_seed_neighbor_floor(
        pruned=pruned,
        self_neighbors=self_neighbors,
        query_vector=[1.0, 0.0],
        floor=2,
        hop_idx=0,
        self_in_frontier=True,
    )
    uuids = [n.uuid for n in result]
    assert "keep-1" in uuids
    assert "self-rel-1" in uuids
    assert "self-rel-2" in uuids


def test_apply_self_seed_neighbor_floor_noop_when_not_first_hop():
    pruned = [_node("keep-1", [1.0, 0.0])]
    self_neighbors = [_node("self-rel-1", [0.9, 0.1])]
    result = apply_self_seed_neighbor_floor(
        pruned=pruned,
        self_neighbors=self_neighbors,
        query_vector=[1.0, 0.0],
        floor=2,
        hop_idx=1,
        self_in_frontier=True,
    )
    assert [n.uuid for n in result] == ["keep-1"]


# --- _fetch_nodes_by_ids uses async for, not await ---


@pytest.mark.asyncio
async def test_fetch_nodes_by_ids_uses_async_generator():
    """db.get_all returns an async generator — must iterate with async for."""
    cfg = _config()

    snap = MagicMock()
    snap.exists = True
    snap.id = "entity-1"
    snap.to_dict.return_value = {
        "node_type": "person",
        "content": "Jack",
        "domain": "entity",
        "weight": 0.55,
        "metadata": "{}",
        "entity_links": [],
        "journal_entry_ids": [],
        "user_id": "global",
    }

    async def _fake_get_all(refs):
        yield snap

    mock_db = MagicMock()
    mock_db.get_all = _fake_get_all
    mock_db.collection.return_value.document.return_value = MagicMock()

    result = await _fetch_nodes_by_ids(mock_db, cfg, ["entity-1"])
    assert "entity-1" in result
    assert result["entity-1"].content == "Jack"


@pytest.mark.asyncio
async def test_get_edge_neighbors_queries_relationships_collection():
    from unittest.mock import MagicMock

    from lethe.graph.traverse import _get_edge_neighbors

    cfg = _config()

    rel_doc = MagicMock()
    rel_doc.id = "rel_abc"
    rel_doc.to_dict.return_value = {
        "subject_uuid": "node-a",
        "predicate": "knows",
        "object_uuid": "node-b",
        "content": "a knows b",
        "weight": 0.8,
        "domain": "general",
        "user_id": "global",
        "journal_entry_ids": [],
    }

    async def fake_stream():
        yield rel_doc

    mock_query = MagicMock()
    mock_query.where.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.stream = fake_stream

    mock_col = MagicMock()
    mock_col.where.return_value = mock_query

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_col

    edges = await _get_edge_neighbors(mock_db, cfg, "node-a", "global")

    mock_db.collection.assert_called_with(cfg.lethe_relationships_collection)
    assert len(edges) >= 1
    assert edges[0].predicate == "knows"
    assert edges[0].uuid == "rel_abc"


def test_passes_source_filter_none_allows_all():
    from lethe.graph.traverse import _passes_source_filter

    node_with_source = Node(uuid="a", node_type="chunk", content="x", source="corpus-A")
    node_no_source = Node(uuid="b", node_type="person", content="Alice")
    assert _passes_source_filter(node_with_source, None) is True
    assert _passes_source_filter(node_no_source, None) is True


def test_passes_source_filter_entity_node_always_passes():
    from lethe.graph.traverse import _passes_source_filter

    entity = Node(uuid="e", node_type="person", content="Alice")  # source defaults to None
    assert _passes_source_filter(entity, "corpus-A") is True


def test_passes_source_filter_matching_source_passes():
    from lethe.graph.traverse import _passes_source_filter

    node = Node(uuid="c", node_type="chunk", content="x", source="corpus-A")
    assert _passes_source_filter(node, "corpus-A") is True


def test_passes_source_filter_non_matching_source_excluded():
    from lethe.graph.traverse import _passes_source_filter

    node = Node(uuid="d", node_type="chunk", content="x", source="corpus-B")
    assert _passes_source_filter(node, "corpus-A") is False


def test_graph_expand_request_accepts_source_filter():
    from lethe.models.node import GraphExpandRequest

    req = GraphExpandRequest(seed_ids=["abc"], source_filter="corpus-123")
    assert req.source_filter == "corpus-123"


def test_graph_expand_request_source_filter_defaults_none():
    from lethe.models.node import GraphExpandRequest

    req = GraphExpandRequest(seed_ids=["abc"])
    assert req.source_filter is None


@pytest.mark.asyncio
async def test_graph_expand_excludes_non_matching_source_nodes(mock_embedder):
    """graph_expand with source_filter excludes nodes from non-matching corpora."""
    from unittest.mock import MagicMock

    from lethe.graph.traverse import graph_expand

    cfg = _config()

    snap_map = {
        "seed-entity-1": {
            "node_type": "person",
            "content": "Alice",
            "domain": "general",
            "weight": 0.55,
            "metadata": "{}",
            "journal_entry_ids": [],
            "user_id": "global",
            "source": None,
        },
        "chunk-corpus-a": {
            "node_type": "chunk",
            "content": "Alice's notes from corpus A",
            "domain": "general",
            "weight": 0.4,
            "metadata": "{}",
            "journal_entry_ids": [],
            "user_id": "global",
            "source": "corpus-A",
        },
        "chunk-corpus-b": {
            "node_type": "chunk",
            "content": "Alice's notes from corpus B",
            "domain": "general",
            "weight": 0.4,
            "metadata": "{}",
            "journal_entry_ids": [],
            "user_id": "global",
            "source": "corpus-B",
        },
    }

    async def fake_get_all(refs):
        for ref in refs:
            data = snap_map.get(ref.id)
            if data:
                snap = MagicMock()
                snap.exists = True
                snap.id = ref.id
                snap.to_dict.return_value = data
                yield snap

    edge_snap_a = MagicMock()
    edge_snap_a.id = "rel-a"
    edge_snap_a.to_dict.return_value = {
        "subject_uuid": "seed-entity-1",
        "predicate": "references",
        "object_uuid": "chunk-corpus-a",
        "content": "",
        "weight": 0.8,
        "domain": "general",
        "user_id": "global",
        "journal_entry_ids": [],
    }
    edge_snap_b = MagicMock()
    edge_snap_b.id = "rel-b"
    edge_snap_b.to_dict.return_value = {
        "subject_uuid": "seed-entity-1",
        "predicate": "references",
        "object_uuid": "chunk-corpus-b",
        "content": "",
        "weight": 0.8,
        "domain": "general",
        "user_id": "global",
        "journal_entry_ids": [],
    }

    # subject_uuid query yields both edges; object_uuid query yields nothing
    async def fake_stream_subject():
        yield edge_snap_a
        yield edge_snap_b

    async def fake_stream_object():
        return
        yield

    query_calls = 0

    def build_query(col):
        q = MagicMock()

        def _where(filter=None):
            nonlocal query_calls
            inner = MagicMock()
            inner.where.side_effect = _where
            inner.limit.return_value = inner

            # subject_uuid queries return edges; object_uuid queries return nothing
            field = getattr(filter, "field_path", None) or getattr(filter, "_field", None)
            if field == "subject_uuid":
                inner.stream = fake_stream_subject
            else:
                inner.stream = fake_stream_object
            return inner

        q.where.side_effect = _where
        q.limit.return_value = q
        return q

    def _make_doc_ref(uid):
        ref = MagicMock()
        ref.id = uid
        return ref

    node_col = MagicMock()
    node_col.document.side_effect = _make_doc_ref

    rel_col = build_query(None)
    rel_col.where.side_effect = lambda filter=None: build_query(None).where(filter=filter)

    def _collection(name):
        if "relationship" in name:
            return rel_col
        return node_col

    mock_db = MagicMock()
    mock_db.get_all = fake_get_all
    mock_db.collection.side_effect = _collection

    result = await graph_expand(
        db=mock_db,
        embedder=mock_embedder,
        config=cfg,
        seed_ids=["seed-entity-1"],
        query=None,
        hops=1,
        limit_per_edge=20,
        user_id="global",
        source_filter="corpus-A",
    )

    assert "chunk-corpus-b" not in result.nodes, "corpus-B node should be excluded"
    assert "seed-entity-1" in result.nodes, "seed node (source=None) should always pass"
