from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from lethe.models.node import Node


def _config():
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test"}, clear=True):
        from lethe.config import Config

        return Config(_env_file=None)


def _entity(uuid: str, journal_ids: list[str]) -> Node:
    return Node(uuid=uuid, node_type="person", content=uuid, journal_entry_ids=journal_ids)


def _log(uuid: str, content: str) -> Node:
    return Node(uuid=uuid, node_type="log", content=content)


@pytest.mark.asyncio
async def test_fetch_source_logs_returns_log_nodes_for_entities():
    from lethe.graph.source_fetch import fetch_source_logs

    cfg = _config()
    entity_nodes = {
        "ent-1": _entity("ent-1", ["log-a", "log-b"]),
    }

    log_snap_a = MagicMock()
    log_snap_a.exists = True
    log_snap_a.id = "log-a"
    log_snap_a.to_dict.return_value = {
        "node_type": "log",
        "content": "First entry",
        "domain": "general",
        "weight": 0.3,
        "metadata": "{}",
        "journal_entry_ids": [],
        "user_id": "global",
    }

    log_snap_b = MagicMock()
    log_snap_b.exists = True
    log_snap_b.id = "log-b"
    log_snap_b.to_dict.return_value = {
        "node_type": "log",
        "content": "Second entry",
        "domain": "general",
        "weight": 0.3,
        "metadata": "{}",
        "journal_entry_ids": [],
        "user_id": "global",
    }

    async def _fake_get_all(refs):
        yield log_snap_a
        yield log_snap_b

    mock_db = MagicMock()
    mock_db.get_all = _fake_get_all
    mock_db.collection.return_value.document.return_value = MagicMock()

    result = await fetch_source_logs(entity_nodes, mock_db, cfg)

    assert "ent-1" in result
    contents = [n.content for n in result["ent-1"]]
    assert "First entry" in contents or "Second entry" in contents


@pytest.mark.asyncio
async def test_fetch_source_logs_respects_max_per_node():
    from lethe.graph.source_fetch import fetch_source_logs

    cfg = _config()
    entity_nodes = {
        "ent-1": _entity("ent-1", ["log-1", "log-2", "log-3", "log-4", "log-5"]),
    }

    fetched_ids: list[str] = []

    async def _fake_get_all(refs):
        for ref in refs:
            fetched_ids.append(ref.id)
        return
        yield

    mock_db = MagicMock()
    mock_db.get_all = _fake_get_all
    mock_db.collection.return_value.document = lambda uid: MagicMock(id=uid)

    await fetch_source_logs(entity_nodes, mock_db, cfg, max_per_node=2)

    assert set(fetched_ids) <= {"log-4", "log-5"}


@pytest.mark.asyncio
async def test_fetch_source_logs_skips_log_entity_nodes():
    from lethe.graph.source_fetch import fetch_source_logs

    cfg = _config()
    entity_nodes = {
        "log-node": Node(
            uuid="log-node",
            node_type="log",
            content="raw entry",
            journal_entry_ids=["should-not-fetch"],
        ),
    }

    async def _fake_get_all(refs):
        assert False, "should not have been called"
        yield

    mock_db = MagicMock()
    mock_db.get_all = _fake_get_all

    result = await fetch_source_logs(entity_nodes, mock_db, cfg)
    assert result == {}


@pytest.mark.asyncio
async def test_fetch_source_logs_empty_nodes():
    from lethe.graph.source_fetch import fetch_source_logs

    cfg = _config()

    async def _fake_get_all(refs):
        return
        yield

    mock_db = MagicMock()
    mock_db.get_all = _fake_get_all

    result = await fetch_source_logs({}, mock_db, cfg)
    assert result == {}
