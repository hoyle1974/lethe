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
