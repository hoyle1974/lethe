import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from lethe.graph.canonical_map import CanonicalMap


def _make_test_client(mock_embedder=None, mock_llm=None, mock_db=None):
    from lethe.main import app
    from lethe.config import Config

    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-proj"}, clear=True):
        cfg = Config()

    app.state.config = cfg
    app.state.db = mock_db or MagicMock()
    app.state.embedder = mock_embedder
    app.state.llm = mock_llm
    app.state.canonical_map = CanonicalMap()
    return TestClient(app, raise_server_exceptions=True)


def test_health():
    client = _make_test_client()
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ingest_status_none_returns_entry_uuid(mock_embedder, mock_llm):
    """When LLM returns status:none, ingest stores the log and returns entry_uuid."""
    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
    mock_doc_ref.update = AsyncMock()

    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.stream = \
        AsyncMock(return_value=_async_iter([]))

    # LLM returns no triples
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post("/v1/ingest", json={"text": "Hello world"})
    assert resp.status_code == 200
    data = resp.json()
    assert "entry_uuid" in data
    assert isinstance(data["nodes_created"], list)
    assert isinstance(data["relationships_created"], list)


def _async_iter(items):
    """Return an async iterator over items."""
    async def _gen():
        for item in items:
            yield item
    return _gen()


def test_get_node_not_found(mock_embedder, mock_llm):
    mock_snap = AsyncMock()
    mock_snap.exists = False
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value.get = AsyncMock(return_value=mock_snap)

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.get("/v1/nodes/nonexistent-uuid")
    assert resp.status_code == 404


def test_get_node_types(mock_embedder, mock_llm):
    mock_doc_snap = MagicMock()
    mock_doc_snap.exists = False
    mock_doc_ref = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=mock_doc_snap)
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.get("/v1/node-types")
    assert resp.status_code == 200
    data = resp.json()
    assert "node_types" in data
    assert "allowed_predicates" in data
    assert "generic" in data["node_types"]


def test_delete_node_method_not_allowed(mock_embedder, mock_llm):
    client = _make_test_client(mock_embedder, mock_llm)
    resp = client.delete("/v1/nodes/some-uuid")
    assert resp.status_code == 405


def test_delete_entry_method_not_allowed(mock_embedder, mock_llm):
    client = _make_test_client(mock_embedder, mock_llm)
    resp = client.delete("/v1/entries/some-uuid")
    assert resp.status_code == 405
