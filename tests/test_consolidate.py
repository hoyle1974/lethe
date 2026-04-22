"""Tests for run_consolidation and POST /v1/admin/consolidate."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from lethe.graph.canonical_map import CanonicalMap
from lethe.graph.consolidate import ConsolidationResponse, run_consolidation
from lethe.models.node import IngestResponse
from tests.conftest import MockLLM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _async_iter(items):
    """Return an async generator over items."""

    async def _gen():
        for item in items:
            yield item

    return _gen()


def _config():
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test"}, clear=True):
        from lethe.config import Config

        return Config(_env_file=None)


def _make_mock_doc(content: str, node_type: str = "log", user_id: str = "global"):
    doc = MagicMock()
    doc.id = f"doc_{content[:8]}"
    doc.to_dict.return_value = {
        "content": content,
        "node_type": node_type,
        "user_id": user_id,
        "updated_at": "2026-01-01",
    }
    return doc


def _make_db_with_stream(docs):
    """Build a MagicMock db whose chained query returns the given docs.

    consolidate.py calls ``q.stream()`` and then does ``async for doc in ...``.
    Using AsyncMock wraps the return value in a coroutine, which is not an
    async iterable.  We need a plain MagicMock whose side_effect returns a
    fresh async generator each time it is called.
    """
    mock_db = MagicMock()

    def _stream_side_effect(*args, **kwargs):
        return _async_iter(docs)

    stream_mock = MagicMock(side_effect=_stream_side_effect)
    (
        mock_db.collection.return_value.where.return_value.where.return_value.order_by.return_value.limit.return_value  # noqa: E501
    ).stream = stream_mock
    return mock_db


def _make_test_client(mock_embedder=None, mock_llm=None, mock_db=None):
    from lethe.deps import get_canonical_map
    from lethe.main import app

    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-proj"}, clear=True):
        from lethe.config import Config

        cfg = Config()

    app.state.config = cfg
    app.state.db = mock_db or MagicMock()
    app.state.embedder = mock_embedder
    app.state.llm = mock_llm
    app.dependency_overrides[get_canonical_map] = lambda: CanonicalMap()
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Unit tests for run_consolidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_consolidation_returns_empty_when_no_logs(mock_embedder, mock_llm):
    """When there are no log docs, run_consolidation returns an empty response."""
    mock_db = _make_db_with_stream([])
    config = _config()

    result = await run_consolidation(
        db=mock_db,
        embedder=mock_embedder,
        llm=mock_llm,
        config=config,
        canonical_map=CanonicalMap(),
        user_id="global",
    )

    assert result == ConsolidationResponse()
    assert result.statements == []
    assert result.ingest_results == []


@pytest.mark.asyncio
async def test_run_consolidation_returns_empty_when_llm_fails(mock_embedder):
    """When the LLM raises, run_consolidation returns an empty response."""
    doc = _make_mock_doc("I went for a run")
    mock_db = _make_db_with_stream([doc])
    config = _config()

    class BrokenLLM:
        async def dispatch(self, req):
            raise RuntimeError("LLM unavailable")

    result = await run_consolidation(
        db=mock_db,
        embedder=mock_embedder,
        llm=BrokenLLM(),
        config=config,
        canonical_map=CanonicalMap(),
        user_id="global",
    )

    assert result == ConsolidationResponse()
    assert result.statements == []
    assert result.ingest_results == []


@pytest.mark.asyncio
async def test_run_consolidation_returns_statements_and_ingest_results(mock_embedder):
    """Happy path: 2 log docs -> LLM returns 2 statements -> 2 ingest calls."""
    doc1 = _make_mock_doc("I prefer dark roast coffee")
    doc2 = _make_mock_doc("I work remotely")
    mock_db = _make_db_with_stream([doc1, doc2])
    config = _config()

    class TwoLineLLM:
        async def dispatch(self, req):
            return "User prefers dark roast coffee\nUser works remotely"

    fake_ingest = IngestResponse(
        entry_uuid="fake-uuid",
        nodes_created=["n1"],
        nodes_updated=[],
        relationships_created=[],
    )

    with patch(
        "lethe.graph.consolidate.run_ingest", new_callable=AsyncMock, return_value=fake_ingest
    ):
        result = await run_consolidation(
            db=mock_db,
            embedder=mock_embedder,
            llm=TwoLineLLM(),
            config=config,
            canonical_map=CanonicalMap(),
            user_id="global",
        )

    assert result.statements == ["User prefers dark roast coffee", "User works remotely"]
    assert len(result.ingest_results) == 2
    assert all(isinstance(r, IngestResponse) for r in result.ingest_results)


# ---------------------------------------------------------------------------
# Integration test for POST /v1/admin/consolidate
# ---------------------------------------------------------------------------


def test_post_consolidate_returns_201_with_statements(mock_embedder):
    """POST /v1/admin/consolidate returns 201 and statements list."""
    doc = _make_mock_doc("I love coffee")
    mock_db = _make_db_with_stream([doc])
    mock_llm = MockLLM("User prefers coffee")

    known_response = ConsolidationResponse(
        statements=["User prefers coffee"],
        ingest_results=[],
    )

    with patch(
        "lethe.routers.admin.run_consolidation",
        new_callable=AsyncMock,
        return_value=known_response,
    ):
        client = _make_test_client(mock_embedder, mock_llm, mock_db)
        resp = client.post("/v1/admin/consolidate", json={"user_id": "test_user"})

    assert resp.status_code == 201
    data = resp.json()
    assert "statements" in data
    assert data["statements"] == ["User prefers coffee"]
