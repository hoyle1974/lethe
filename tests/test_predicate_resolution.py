import pytest

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
