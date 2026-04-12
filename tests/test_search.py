from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lethe.graph.search import (
    cosine_similarity,
    doc_to_node,
    effective_distance_decay,
    parse_to_utc,
)
from lethe.models.node import Node

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
        "vector_distance": 0.12,  # must be stripped, not cause validation error
    }
    node = doc_to_node("abc123", data)
    assert node.uuid == "abc123"
    assert node.content == "Jack"


def test_doc_to_node_parses_iso_timestamps():
    data = {
        "node_type": "log",
        "content": "x",
        "metadata": "{}",
        "entity_links": [],
        "user_id": "global",
        "updated_at": "2024-01-15T12:00:00+00:00",
        "created_at": "2024-01-01T00:00:00Z",
    }
    n = doc_to_node("id1", data)
    assert n.updated_at is not None
    assert n.updated_at.tzinfo is not None
    assert n.created_at is not None


def test_parse_to_utc_naive_string():
    dt = parse_to_utc("2024-06-01T00:00:00")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_effective_distance_decay_older_log_ranks_worse():
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    old = Node(
        uuid="a",
        node_type="log",
        content="old",
        updated_at=now - timedelta(days=30),
    )
    fresh = Node(
        uuid="b",
        node_type="log",
        content="new",
        updated_at=now - timedelta(days=1),
    )
    raw = 0.2
    assert effective_distance_decay(old, raw, now) > effective_distance_decay(fresh, raw, now)


def test_execute_search_ordering_excludes_tombstone_weight():
    """Ghost-edge fix: tombstoned nodes have weight 0.0 and must not appear in results."""
    from lethe.graph.search import effective_distance_decay

    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    dead = Node(uuid="d", node_type="log", content="", weight=0.0, updated_at=now)
    alive = Node(uuid="a", node_type="log", content="", weight=0.3, updated_at=now)
    decorated = [
        (dead, effective_distance_decay(dead, 0.1, now)),
        (alive, effective_distance_decay(alive, 0.5, now)),
    ]
    decorated.sort(key=lambda x: x[1])
    ordered = [n for n, _ in decorated if n.weight > 0.0]
    assert [n.uuid for n in ordered] == ["a"]


def test_effective_distance_reinforcement_reduces_effective_distance():
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    base = Node(
        uuid="a",
        node_type="entity",
        content="e",
        updated_at=now,
        journal_entry_ids=[],
    )
    reinforced = Node(
        uuid="b",
        node_type="entity",
        content="e",
        updated_at=now,
        journal_entry_ids=["x"] * 10,
    )
    raw = 0.3
    assert effective_distance_decay(reinforced, raw, now) < effective_distance_decay(base, raw, now)
