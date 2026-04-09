from __future__ import annotations

import pytest

from lethe.graph.contradiction import evaluate_relationship_supersedes
from lethe.infra.llm import LLMRequest


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def dispatch(self, req: LLMRequest) -> str:
        return self.reply


@pytest.mark.asyncio
async def test_evaluate_supersedes_finds_uuid_in_response():
    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    llm = FakeLLM(f"Supersede {uid} because moved.")
    out = await evaluate_relationship_supersedes(
        llm, "Alex lives_in NY", [(uid, "Alex lives_in SF")]
    )
    assert out == uid


@pytest.mark.asyncio
async def test_evaluate_supersedes_returns_none_without_match():
    llm = FakeLLM("none")
    out = await evaluate_relationship_supersedes(
        llm, "Alex likes pizza", [("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "Alex likes pasta")]
    )
    assert out is None
