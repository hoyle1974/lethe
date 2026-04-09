from __future__ import annotations
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from lethe.graph.traverse import prune_frontier_by_similarity, _fetch_nodes_by_ids
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


# --- _fetch_nodes_by_ids uses async for, not await ---

@pytest.mark.asyncio
async def test_fetch_nodes_by_ids_uses_async_generator():
    """db.get_all returns an async generator — must iterate with async for."""
    cfg = _config()

    snap = MagicMock()
    snap.exists = True
    snap.id = "entity-1"
    snap.to_dict.return_value = {
        "node_type": "person", "content": "Jack", "domain": "entity",
        "weight": 0.55, "metadata": "{}", "entity_links": [],
        "journal_entry_ids": [], "user_id": "global",
    }

    async def _fake_get_all(refs):
        yield snap

    mock_db = MagicMock()
    mock_db.get_all = _fake_get_all
    mock_db.collection.return_value.document.return_value = MagicMock()

    result = await _fetch_nodes_by_ids(mock_db, cfg, ["entity-1"])
    assert "entity-1" in result
    assert result["entity-1"].content == "Jack"
