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
