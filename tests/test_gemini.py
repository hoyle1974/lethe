from types import SimpleNamespace

import pytest

from lethe.config import Config
from lethe.infra.gemini import GeminiLLM
from lethe.infra.llm import LLMRequest


class _FakeModel:
    def __init__(self, response):
        self._response = response

    def generate_content(self, *_args, **_kwargs):
        return self._response


@pytest.mark.asyncio
async def test_dispatch_returns_text_when_response_has_text(monkeypatch):
    response = SimpleNamespace(text="status: none\ntriples:\n")
    monkeypatch.setattr(
        "lethe.infra.gemini.GenerativeModel",
        lambda *_args, **_kwargs: _FakeModel(response),
    )

    llm = GeminiLLM(Config(google_cloud_project="test-project"))
    result = await llm.dispatch(LLMRequest(system_prompt="", user_prompt="hello"))

    assert result == "status: none\ntriples:\n"


@pytest.mark.asyncio
async def test_dispatch_handles_max_tokens_response_without_text_parts(monkeypatch, caplog):
    class _NoTextResponse:
        @property
        def text(self):
            raise ValueError("Cannot get the response text.")

    response = _NoTextResponse()
    response.candidates = [
        SimpleNamespace(
            finish_reason="MAX_TOKENS",
            content=SimpleNamespace(role="model", parts=[]),
        )
    ]
    monkeypatch.setattr(
        "lethe.infra.gemini.GenerativeModel",
        lambda *_args, **_kwargs: _FakeModel(response),
    )

    llm = GeminiLLM(Config(google_cloud_project="test-project"))
    result = await llm.dispatch(LLMRequest(system_prompt="", user_prompt="hello"))

    assert result == "status: none\ntriples:\n"
    assert "truncated at max tokens" in caplog.text
