from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lethe.graph.canonical_map import CanonicalMap
from lethe.graph.extraction import RefineryTriple
from lethe.graph.ingest import _process_triple
from lethe.graph.predicate_resolution import _parse_response, resolve_new_predicate
from tests.conftest import MockLLM

EXISTING = ["works_at", "lives_in", "knows", "related_to"]


@pytest.mark.asyncio
async def test_returns_existing_when_llm_matches():
    llm = MockLLM("EXISTING: works_at")
    result = await resolve_new_predicate(
        llm=llm,
        proposed="employed_by",
        triple_subject="Alice",
        triple_object="Anthropic",
        existing=EXISTING,
    )
    assert result == "works_at"


@pytest.mark.asyncio
async def test_returns_proposed_when_llm_approves_novel():
    llm = MockLLM("NEW: approved")
    result = await resolve_new_predicate(
        llm=llm,
        proposed="has_child",
        triple_subject="Alice",
        triple_object="Bob",
        existing=EXISTING,
    )
    assert result == "has_child"


@pytest.mark.asyncio
async def test_falls_back_to_proposed_on_llm_error():
    class FailingLLM:
        async def dispatch(self, req):
            raise RuntimeError("LLM unavailable")

    result = await resolve_new_predicate(
        llm=FailingLLM(),
        proposed="mentors",
        triple_subject="Alice",
        triple_object="Bob",
        existing=EXISTING,
    )
    assert result == "mentors"


@pytest.mark.asyncio
async def test_rejects_hallucinated_existing_predicate():
    llm = MockLLM("EXISTING: invented_predicate")
    result = await resolve_new_predicate(
        llm=llm,
        proposed="mentors",
        triple_subject="Alice",
        triple_object="Bob",
        existing=EXISTING,
    )
    assert result == "mentors"


@pytest.mark.asyncio
async def test_returns_proposed_when_existing_list_empty():
    llm = MockLLM("EXISTING: works_at")
    result = await resolve_new_predicate(
        llm=llm,
        proposed="mentors",
        triple_subject="Alice",
        triple_object="Bob",
        existing=[],
    )
    assert result == "mentors"


def test_parse_response_existing():
    assert _parse_response("EXISTING: works_at", ["works_at", "knows"], "employed_by") == "works_at"


def test_parse_response_novel():
    assert _parse_response("NEW: approved", ["works_at"], "has_child") == "has_child"


def test_parse_response_hallucinated():
    assert _parse_response("EXISTING: fake_pred", ["works_at"], "mentors") == "mentors"


def test_parse_response_empty():
    assert _parse_response("", ["works_at"], "mentors") == "mentors"


def test_parse_response_case_insensitive():
    assert _parse_response("existing: works_at", ["works_at"], "employed_by") == "works_at"


@pytest.mark.asyncio
async def test_process_triple_redirects_new_predicate_to_existing():
    """When resolver maps NEW:employed_by → works_at, the edge uses works_at
    and append_predicate is not called."""
    triple = RefineryTriple(
        subject="Alice",
        predicate="NEW:employed_by",
        object="Anthropic",
        subject_type="person",
        object_type="generic",
    )
    assert triple.is_new_predicate is True
    assert triple.canonical_predicate == "employed_by"

    canonical_map = CanonicalMap()  # includes "works_at" in defaults
    resolver_llm = MockLLM("EXISTING: works_at")

    mock_db = MagicMock()
    mock_config = MagicMock()
    mock_config.lethe_collection = "nodes"
    mock_config.lethe_relationships_collection = "relationships"

    with (
        patch("lethe.graph.ingest.append_predicate", new_callable=AsyncMock) as mock_append,
        patch("lethe.graph.ingest._resolve_term", new_callable=AsyncMock) as mock_resolve,
        patch(
            "lethe.graph.ingest._get_or_create_entity_node", new_callable=AsyncMock
        ) as mock_entity,
        patch("lethe.graph.ingest.create_relationship_node", new_callable=AsyncMock) as mock_rel,
    ):
        mock_resolve.return_value = {
            "text": "Alice",
            "existing_uuid": None,
            "resolved_type": "person",
        }
        fake_node = MagicMock()
        fake_node.uuid = "entity_abc"
        fake_node.content = "Alice"
        mock_entity.return_value = (False, fake_node)
        mock_rel.return_value = "rel_xyz"

        await _process_triple(
            db=mock_db,
            embedder=MagicMock(),
            llm=resolver_llm,
            config=mock_config,
            triple=triple,
            entry_uuid="entry_001",
            ts="2026-01-01T00:00:00+00:00",
            user_id="global",
            nodes_created=[],
            nodes_updated=[],
            relationships_created=[],
            canonical_map=canonical_map,
        )

    mock_append.assert_not_called()
    _, kwargs = mock_rel.call_args
    assert kwargs["predicate"] == "works_at"
    assert "employed_by" not in canonical_map.allowed_predicates


@pytest.mark.asyncio
async def test_process_triple_appends_genuinely_novel_predicate():
    """When resolver approves NEW:mentors, append_predicate is called and map grows."""
    triple = RefineryTriple(
        subject="Alice",
        predicate="NEW:mentors",
        object="Bob",
        subject_type="person",
        object_type="person",
    )

    canonical_map = CanonicalMap()
    assert "mentors" not in canonical_map.allowed_predicates

    resolver_llm = MockLLM("NEW: approved")

    mock_db = MagicMock()
    mock_config = MagicMock()
    mock_config.lethe_collection = "nodes"
    mock_config.lethe_relationships_collection = "relationships"

    with (
        patch("lethe.graph.ingest.append_predicate", new_callable=AsyncMock) as mock_append,
        patch("lethe.graph.ingest._resolve_term", new_callable=AsyncMock) as mock_resolve,
        patch(
            "lethe.graph.ingest._get_or_create_entity_node", new_callable=AsyncMock
        ) as mock_entity,
        patch("lethe.graph.ingest.create_relationship_node", new_callable=AsyncMock) as mock_rel,
    ):
        mock_resolve.return_value = {
            "text": "Alice",
            "existing_uuid": None,
            "resolved_type": "person",
        }
        fake_node = MagicMock()
        fake_node.uuid = "entity_abc"
        fake_node.content = "Alice"
        mock_entity.return_value = (False, fake_node)
        mock_rel.return_value = "rel_xyz"

        await _process_triple(
            db=mock_db,
            embedder=MagicMock(),
            llm=resolver_llm,
            config=mock_config,
            triple=triple,
            entry_uuid="entry_002",
            ts="2026-01-01T00:00:00+00:00",
            user_id="global",
            nodes_created=[],
            nodes_updated=[],
            relationships_created=[],
            canonical_map=canonical_map,
        )

    mock_append.assert_called_once_with(mock_db, "mentors")
    assert "mentors" in canonical_map.allowed_predicates
    _, kwargs = mock_rel.call_args
    assert kwargs["predicate"] == "mentors"
