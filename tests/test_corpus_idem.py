"""Tests for idempotent corpus ingestion helpers."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from lethe.graph.corpus import (
    _content_hash,
    _get_existing_chunk_ids,
    _tombstone_chunks_for_document,
    _upsert_corpus_node,
    _upsert_document_node,
    run_corpus_ingest,
    stable_corpus_node_id,
    stable_document_id,
)
from lethe.models.node import DocumentItem

# ---------------------------------------------------------------------------
# Stable ID helpers
# ---------------------------------------------------------------------------


def test_stable_document_id_is_deterministic():
    a = stable_document_id("corp1", "main.py")
    b = stable_document_id("corp1", "main.py")
    assert a == b


def test_stable_document_id_differs_by_corpus():
    assert stable_document_id("corp1", "main.py") != stable_document_id("corp2", "main.py")


def test_stable_document_id_differs_by_filename():
    assert stable_document_id("corp1", "main.py") != stable_document_id("corp1", "util.py")


def test_stable_document_id_has_doc_prefix():
    assert stable_document_id("corp1", "main.py").startswith("doc_")


def test_stable_corpus_node_id_is_deterministic():
    assert stable_corpus_node_id("corp1") == stable_corpus_node_id("corp1")


def test_stable_corpus_node_id_differs_by_corpus():
    assert stable_corpus_node_id("corp1") != stable_corpus_node_id("corp2")


def test_stable_corpus_node_id_has_corpus_prefix():
    assert stable_corpus_node_id("corp1").startswith("corpus_")


def test_content_hash_is_deterministic():
    assert _content_hash("hello") == _content_hash("hello")


def test_content_hash_differs_for_different_text():
    assert _content_hash("hello") != _content_hash("world")


# ---------------------------------------------------------------------------
# _upsert_document_node
# ---------------------------------------------------------------------------


def _make_doc_mock_db(snap_exists: bool, metadata_json: str | None = None):
    mock_snap = MagicMock()
    mock_snap.exists = snap_exists
    mock_snap.get = MagicMock(return_value=metadata_json)

    mock_doc_ref = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=mock_snap)
    mock_doc_ref.set = AsyncMock()
    mock_doc_ref.update = AsyncMock()

    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref

    mock_config = MagicMock()
    mock_config.lethe_collection = "nodes"

    return mock_db, mock_config, mock_doc_ref


class _MockEmbedder:
    async def embed(self, text, task_type="RETRIEVAL_DOCUMENT"):
        return [0.1] * 768


@pytest.mark.asyncio
async def test_upsert_document_node_new_returns_is_new_true():
    mock_db, mock_config, mock_doc_ref = _make_doc_mock_db(snap_exists=False)
    doc_id, is_new, is_changed = await _upsert_document_node(
        db=mock_db,
        embedder=_MockEmbedder(),
        config=mock_config,
        text="hello world",
        filename="test.py",
        corpus_id="corp1",
        user_id="global",
        domain="general",
        ts="2026-01-01T00:00:00Z",
    )
    assert doc_id == stable_document_id("corp1", "test.py")
    assert is_new is True
    assert is_changed is True
    mock_doc_ref.set.assert_awaited_once()
    mock_doc_ref.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_document_node_unchanged_skips():
    text = "hello world"
    meta = json.dumps({"content_hash": _content_hash(text)})
    mock_db, mock_config, mock_doc_ref = _make_doc_mock_db(snap_exists=True, metadata_json=meta)

    doc_id, is_new, is_changed = await _upsert_document_node(
        db=mock_db,
        embedder=_MockEmbedder(),
        config=mock_config,
        text=text,
        filename="test.py",
        corpus_id="corp1",
        user_id="global",
        domain="general",
        ts="2026-01-01T00:00:00Z",
    )
    assert is_new is False
    assert is_changed is False
    mock_doc_ref.set.assert_not_awaited()
    mock_doc_ref.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_document_node_changed_updates():
    old_meta = json.dumps({"content_hash": _content_hash("old content")})
    mock_db, mock_config, mock_doc_ref = _make_doc_mock_db(snap_exists=True, metadata_json=old_meta)

    doc_id, is_new, is_changed = await _upsert_document_node(
        db=mock_db,
        embedder=_MockEmbedder(),
        config=mock_config,
        text="new content",
        filename="test.py",
        corpus_id="corp1",
        user_id="global",
        domain="general",
        ts="2026-01-01T00:00:00Z",
    )
    assert is_new is False
    assert is_changed is True
    mock_doc_ref.update.assert_awaited_once()
    mock_doc_ref.set.assert_not_awaited()


# ---------------------------------------------------------------------------
# _upsert_corpus_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_corpus_node_new():
    mock_db, mock_config, mock_doc_ref = _make_doc_mock_db(snap_exists=False)

    node_id, is_new = await _upsert_corpus_node(
        db=mock_db,
        embedder=_MockEmbedder(),
        config=mock_config,
        corpus_id="corp1",
        filenames=["a.py", "b.py"],
        user_id="global",
        domain="general",
        ts="2026-01-01T00:00:00Z",
    )
    assert node_id == stable_corpus_node_id("corp1")
    assert is_new is True
    mock_doc_ref.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_corpus_node_existing_updates():
    mock_db, mock_config, mock_doc_ref = _make_doc_mock_db(snap_exists=True)

    node_id, is_new = await _upsert_corpus_node(
        db=mock_db,
        embedder=_MockEmbedder(),
        config=mock_config,
        corpus_id="corp1",
        filenames=["a.py", "b.py"],
        user_id="global",
        domain="general",
        ts="2026-01-01T00:00:00Z",
    )
    assert node_id == stable_corpus_node_id("corp1")
    assert is_new is False
    mock_doc_ref.update.assert_awaited_once()
    mock_doc_ref.set.assert_not_awaited()


# ---------------------------------------------------------------------------
# _tombstone_chunks_for_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tombstone_chunks_sets_weight_zero():
    chunk1 = MagicMock()
    chunk1.id = "chunk-aaa"
    chunk2 = MagicMock()
    chunk2.id = "chunk-bbb"

    mock_query = MagicMock()
    mock_query.where = MagicMock(return_value=mock_query)
    mock_query.get = AsyncMock(return_value=[chunk1, chunk2])

    mock_update_ref = AsyncMock()
    mock_update_ref.update = AsyncMock()

    mock_col = MagicMock()
    mock_col.where = MagicMock(return_value=mock_query)
    mock_col.document = MagicMock(return_value=mock_update_ref)

    mock_db = MagicMock()
    mock_db.collection = MagicMock(return_value=mock_col)

    mock_config = MagicMock()
    mock_config.lethe_collection = "nodes"

    await _tombstone_chunks_for_document(mock_db, mock_config, "doc-123", "global")

    assert mock_update_ref.update.await_count == 2
    calls = mock_update_ref.update.await_args_list
    assert all(call.args[0] == {"weight": 0.0} for call in calls)


@pytest.mark.asyncio
async def test_tombstone_chunks_empty_returns_gracefully():
    mock_query = MagicMock()
    mock_query.where = MagicMock(return_value=mock_query)
    mock_query.get = AsyncMock(return_value=[])

    mock_col = MagicMock()
    mock_col.where = MagicMock(return_value=mock_query)

    mock_db = MagicMock()
    mock_db.collection = MagicMock(return_value=mock_col)

    mock_config = MagicMock()
    mock_config.lethe_collection = "nodes"

    await _tombstone_chunks_for_document(mock_db, mock_config, "doc-none", "global")
    # No exception raised


# ---------------------------------------------------------------------------
# _get_existing_chunk_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_existing_chunk_ids_returns_active_only():
    alive = MagicMock()
    alive.id = "chunk-alive"
    alive.get = MagicMock(return_value=0.4)

    dead = MagicMock()
    dead.id = "chunk-dead"
    dead.get = MagicMock(return_value=0.0)

    mock_query = MagicMock()
    mock_query.where = MagicMock(return_value=mock_query)
    mock_query.get = AsyncMock(return_value=[alive, dead])

    mock_col = MagicMock()
    mock_col.where = MagicMock(return_value=mock_query)

    mock_db = MagicMock()
    mock_db.collection = MagicMock(return_value=mock_col)

    mock_config = MagicMock()
    mock_config.lethe_collection = "nodes"

    ids = await _get_existing_chunk_ids(mock_db, mock_config, "doc-123", "global")
    assert ids == ["chunk-alive"]


# ---------------------------------------------------------------------------
# run_corpus_ingest — skip unchanged doc (integration-level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_corpus_ingest_skips_unchanged_doc():
    """When content hash matches, no LLM calls are made and existing chunk IDs are reused."""
    text = "Alice works at Acme."
    corpus_id = "test-skip-corp"

    corpus_snap = MagicMock()
    corpus_snap.exists = False
    corpus_ref = AsyncMock()
    corpus_ref.get = AsyncMock(return_value=corpus_snap)
    corpus_ref.set = AsyncMock()
    corpus_ref.update = AsyncMock()

    doc_snap = MagicMock()
    doc_snap.exists = True
    doc_snap.get = MagicMock(return_value=json.dumps({"content_hash": _content_hash(text)}))
    doc_ref = AsyncMock()
    doc_ref.get = AsyncMock(return_value=doc_snap)
    doc_ref.set = AsyncMock()
    doc_ref.update = AsyncMock()

    def _doc_ref_for(doc_id):
        if doc_id == stable_corpus_node_id(corpus_id):
            return corpus_ref
        return doc_ref

    existing_chunk = MagicMock()
    existing_chunk.id = "chunk-reused"
    existing_chunk.get = MagicMock(return_value=0.4)

    mock_query = MagicMock()
    mock_query.where = MagicMock(return_value=mock_query)
    mock_query.get = AsyncMock(return_value=[existing_chunk])

    mock_col = MagicMock()
    mock_col.document = MagicMock(side_effect=_doc_ref_for)
    mock_col.where = MagicMock(return_value=mock_query)

    mock_db = MagicMock()
    mock_db.collection = MagicMock(return_value=mock_col)

    mock_config = MagicMock()
    mock_config.lethe_collection = "nodes"

    dispatch_calls: list = []

    class _TrackingLLM:
        async def dispatch(self, req):
            dispatch_calls.append(req)
            return "status: none"

    result = await run_corpus_ingest(
        db=mock_db,
        embedder=_MockEmbedder(),
        llm=_TrackingLLM(),
        config=mock_config,
        canonical_map=MagicMock(),
        documents=[DocumentItem(text=text, filename="notes.txt")],
        corpus_id=corpus_id,
    )

    assert dispatch_calls == [], "unchanged doc must not trigger any LLM calls"
    assert result.chunk_ids == ["chunk-reused"]
    assert result.total_chunks == 1
    assert stable_document_id(corpus_id, "notes.txt") in result.document_ids
