from __future__ import annotations
import math
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from lethe.graph.search import cosine_similarity, rrf_fuse, keyword_search, doc_to_node
from lethe.models.node import Node


def _config():
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test"}, clear=True):
        from lethe.config import Config
        return Config(_env_file=None)


def _async_iter(items):
    async def _gen():
        for item in items:
            yield item
    return _gen()


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


# --- keyword_search excludes log nodes client-side ---

@pytest.mark.asyncio
async def test_keyword_search_excludes_log_nodes():
    """log nodes must be filtered even without a server-side != filter."""
    cfg = _config()

    log_doc = MagicMock()
    log_doc.id = "log-1"
    log_doc.to_dict.return_value = {
        "node_type": "log", "content": "jack logged in", "user_id": "global",
        "domain": "general", "weight": 0.3, "metadata": "{}",
        "entity_links": [], "journal_entry_ids": [],
    }

    entity_doc = MagicMock()
    entity_doc.id = "entity-1"
    entity_doc.to_dict.return_value = {
        "node_type": "person", "content": "Jack", "user_id": "global",
        "domain": "entity", "weight": 0.55, "metadata": "{}",
        "entity_links": [], "journal_entry_ids": [],
    }

    mock_db = MagicMock()
    mock_db.collection.return_value \
        .where.return_value \
        .limit.return_value \
        .stream = MagicMock(return_value=_async_iter([log_doc, entity_doc]))

    results = await keyword_search(mock_db, cfg, "jack", [], None, "global", 10)
    uuids = [n.uuid for n in results]
    assert "entity-1" in uuids
    assert "log-1" not in uuids


@pytest.mark.asyncio
async def test_keyword_search_case_insensitive():
    cfg = _config()

    doc = MagicMock()
    doc.id = "e1"
    doc.to_dict.return_value = {
        "node_type": "person", "content": "Gloria", "user_id": "global",
        "domain": "entity", "weight": 0.55, "metadata": "{}",
        "entity_links": [], "journal_entry_ids": [],
    }

    mock_db = MagicMock()
    mock_db.collection.return_value \
        .where.return_value \
        .limit.return_value \
        .stream = MagicMock(return_value=_async_iter([doc]))

    results = await keyword_search(mock_db, cfg, "gloria", [], None, "global", 10)
    assert len(results) == 1
    assert results[0].content == "Gloria"
