from unittest.mock import AsyncMock, MagicMock

import pytest

from lethe.graph.canonical_map import (
    DEFAULT_NODE_TYPES,
    DEFAULT_PREDICATES,
    CanonicalMap,
    append_predicate,
    load_canonical_map,
)


@pytest.mark.asyncio
async def test_load_returns_defaults_when_doc_missing():
    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_ref = AsyncMock()
    mock_ref.get = AsyncMock(return_value=mock_doc)
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_ref

    result = await load_canonical_map(mock_db)

    assert result.node_types == DEFAULT_NODE_TYPES
    assert result.allowed_predicates == DEFAULT_PREDICATES


@pytest.mark.asyncio
async def test_load_returns_stored_values():
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "node_types": ["person", "place"],
        "allowed_predicates": ["works_at", "lives_in"],
    }
    mock_ref = AsyncMock()
    mock_ref.get = AsyncMock(return_value=mock_doc)
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_ref

    result = await load_canonical_map(mock_db)

    assert result.node_types == ["person", "place"]
    assert result.allowed_predicates == ["works_at", "lives_in"]


@pytest.mark.asyncio
async def test_append_predicate():
    mock_ref = AsyncMock()
    mock_ref.update = AsyncMock()
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_ref

    await append_predicate(mock_db, "mentors")

    mock_ref.update.assert_called_once()


def test_canonical_map_defaults():
    cm = CanonicalMap()
    assert "person" in cm.node_types
    assert "works_at" in cm.allowed_predicates
