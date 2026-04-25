"""
conftest.py — shared fixtures and mock implementations for tests.
"""

# ---------------------------------------------------------------------------
# Mock embedder and LLM implementations for tests
# ---------------------------------------------------------------------------
import hashlib

import pytest


class MockEmbedder:
    async def embed(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
        h = int(hashlib.md5(text.encode()).hexdigest(), 16)
        base = (h % 1000) / 1000.0
        return [base] * 768

    async def embed_batch(
        self, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT"
    ) -> list[list[float]]:
        return [await self.embed(t, task_type) for t in texts]


class MockLLM:
    def __init__(self, response: str = "status: none"):
        self._response = response

    async def dispatch(self, req) -> str:
        return self._response


@pytest.fixture
def mock_embedder():
    return MockEmbedder()


@pytest.fixture
def mock_llm():
    return MockLLM()
