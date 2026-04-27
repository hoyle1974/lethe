import pytest

from lethe.graph.predicate_resolution import resolve_new_predicate
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
