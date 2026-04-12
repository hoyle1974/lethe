from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lethe.graph.ensure_node import stable_self_id
from lethe.graph.ingest import _looks_like_generated_id, _looks_like_placeholder_term, _resolve_term


def _config():
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test"}, clear=True):
        from lethe.config import Config

        return Config(_env_file=None)


def test_looks_like_generated_id_matches_internal_patterns():
    assert _looks_like_generated_id("entity_3579d6dd3611a4b7e3cbdb79e5a29698b937bb4e")
    assert _looks_like_generated_id("rel_3579d6dd3611a4b7e3cbdb79e5a29698b937bb4e")
    assert _looks_like_generated_id("9f2e6f90-4be1-4e4a-8a69-0f1fdd853b7e")
    assert not _looks_like_generated_id("Project Aegis")


def test_looks_like_placeholder_term_filters_type_labels():
    assert _looks_like_placeholder_term("generic")
    assert _looks_like_placeholder_term("person", node_type="person")
    assert _looks_like_placeholder_term("tool", node_type="tool")
    assert not _looks_like_placeholder_term("Alex Reed", node_type="person")


@pytest.mark.asyncio
async def test_resolve_term_passes_through_human_text():
    cfg = _config()
    db = MagicMock()

    resolved = await _resolve_term(db, cfg, "Jamie", "person")
    assert resolved == {"text": "Jamie", "existing_uuid": None, "resolved_type": None}


@pytest.mark.asyncio
async def test_resolve_term_maps_self_token_to_stable_uuid():
    cfg = _config()
    db = MagicMock()

    resolved = await _resolve_term(db, cfg, "SELF", "person", user_id="alex_reed_2026")
    assert resolved == {
        "text": "Me",
        "existing_uuid": stable_self_id("alex_reed_2026"),
        "resolved_type": "person",
        "self_token": True,
    }


@pytest.mark.asyncio
async def test_resolve_term_rejects_placeholder_value():
    cfg = _config()
    db = MagicMock()

    resolved = await _resolve_term(db, cfg, "generic", "generic")
    assert resolved is None


@pytest.mark.asyncio
async def test_resolve_term_maps_existing_internal_id_to_content():
    cfg = _config()
    snap = MagicMock()
    snap.exists = True
    snap.to_dict.return_value = {"content": "Project Aegis", "node_type": "project"}

    doc_ref = MagicMock()
    doc_ref.get = AsyncMock(return_value=snap)

    collection = MagicMock()
    collection.document.return_value = doc_ref

    db = MagicMock()
    db.collection.return_value = collection

    resolved = await _resolve_term(
        db,
        cfg,
        "entity_3579d6dd3611a4b7e3cbdb79e5a29698b937bb4e",
        "project",
    )
    assert resolved == {
        "text": "Project Aegis",
        "existing_uuid": "entity_3579d6dd3611a4b7e3cbdb79e5a29698b937bb4e",
        "resolved_type": "project",
    }


@pytest.mark.asyncio
async def test_resolve_term_rejects_unresolvable_internal_id():
    cfg = _config()
    snap = SimpleNamespace(exists=False)

    doc_ref = MagicMock()
    doc_ref.get = AsyncMock(return_value=snap)

    collection = MagicMock()
    collection.document.return_value = doc_ref

    db = MagicMock()
    db.collection.return_value = collection

    resolved = await _resolve_term(
        db,
        cfg,
        "entity_3579d6dd3611a4b7e3cbdb79e5a29698b937bb4e",
        "generic",
    )
    assert resolved is None
