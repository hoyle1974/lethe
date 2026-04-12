from lethe.infra.embedder import Embedder
from lethe.infra.llm import LLMDispatcher
from tests.conftest import MockEmbedder, MockLLM


def test_mock_embedder_satisfies_protocol():
    e = MockEmbedder()
    assert isinstance(e, Embedder)


def test_mock_llm_satisfies_protocol():
    llm = MockLLM()
    assert isinstance(llm, LLMDispatcher)
