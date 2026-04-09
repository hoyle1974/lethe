import pytest
from tests.conftest import MockLLM
from lethe.graph.collision import evaluate_fact_collision, evaluate_fact_collision_if_enabled


@pytest.mark.asyncio
async def test_collision_returns_update():
    llm = MockLLM("update")
    result = await evaluate_fact_collision(llm, "Alice works at Acme", "Alice is employed by Acme Corp")
    assert result == "update"


@pytest.mark.asyncio
async def test_collision_returns_insert():
    llm = MockLLM("insert")
    result = await evaluate_fact_collision(llm, "Alice works at Acme", "Bob lives in Paris")
    assert result == "insert"


@pytest.mark.asyncio
async def test_collision_falls_back_to_insert_on_error():
    class FailingLLM:
        async def dispatch(self, req):
            raise RuntimeError("LLM unavailable")

    result = await evaluate_fact_collision(FailingLLM(), "fact1", "fact2")
    assert result == "insert"


@pytest.mark.asyncio
async def test_collision_disabled_returns_insert():
    llm = MockLLM("update")  # would return update if called
    result = await evaluate_fact_collision_if_enabled(llm, "fact1", "fact2", enabled=False)
    assert result == "insert"
