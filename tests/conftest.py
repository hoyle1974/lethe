"""
conftest.py — shared fixtures and module stubs for tests.

Stubs out vertexai (google-cloud-aiplatform) so tests run without
the heavy GCP AI Platform installation.
"""
import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stub out vertexai before any lethe module imports it
# ---------------------------------------------------------------------------
_vertexai = types.ModuleType("vertexai")
_vertexai.init = MagicMock()

_language_models = types.ModuleType("vertexai.language_models")
_TextEmbeddingModel = MagicMock()
_TextEmbeddingInput = MagicMock()
_language_models.TextEmbeddingModel = _TextEmbeddingModel
_language_models.TextEmbeddingInput = _TextEmbeddingInput

_generative_models = types.ModuleType("vertexai.generative_models")
_GenerativeModel = MagicMock()
_GenerationConfig = MagicMock()
_generative_models.GenerativeModel = _GenerativeModel
_generative_models.GenerationConfig = _GenerationConfig
_generative_models.Content = MagicMock()
_generative_models.Part = MagicMock()

sys.modules.setdefault("vertexai", _vertexai)
sys.modules.setdefault("vertexai.language_models", _language_models)
sys.modules.setdefault("vertexai.generative_models", _generative_models)

# ---------------------------------------------------------------------------
# Mock embedder and LLM implementations for tests
# ---------------------------------------------------------------------------
import pytest
import hashlib


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
