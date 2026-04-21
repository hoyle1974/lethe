from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import lethe.infra.gemini as gemini_mod
from lethe.config import Config
from lethe.infra.gemini import GeminiLLM
from lethe.infra.llm import LLMRequest


def _make_client(response):
    """Return a fake genai client whose aio.models.generate_content returns response."""

    async def _generate_content(*_args, **_kwargs):
        return response

    return SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=_generate_content))
    )


def _make_sequence_client(responses):
    """Return a fake client that cycles through responses in order."""
    pending = list(responses)

    class _AioModels:
        call_count = 0

        async def generate_content(self, *_args, **_kwargs):
            _AioModels.call_count += 1
            return pending.pop(0)

    aio_models = _AioModels()
    return SimpleNamespace(aio=SimpleNamespace(models=aio_models)), aio_models


@pytest.mark.asyncio
async def test_dispatch_returns_text_when_response_has_text(monkeypatch):
    response = SimpleNamespace(text="status: none\ntriples:\n")
    monkeypatch.setattr(
        "lethe.infra.gemini._build_gemini_client",
        lambda *_args, **_kwargs: _make_client(response),
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
        "lethe.infra.gemini._build_gemini_client",
        lambda *_args, **_kwargs: _make_client(response),
    )

    llm = GeminiLLM(Config(google_cloud_project="test-project"))
    result = await llm.dispatch(LLMRequest(system_prompt="", user_prompt="hello"))

    assert result == "status: none\ntriples:\n"
    assert "truncated at max tokens" in caplog.text


@pytest.mark.asyncio
async def test_dispatch_retries_max_tokens_empty_response_once(monkeypatch):
    class _NoTextResponse:
        @property
        def text(self):
            raise ValueError("Cannot get the response text.")

    first = _NoTextResponse()
    first.candidates = [
        SimpleNamespace(
            finish_reason="MAX_TOKENS",
            content=SimpleNamespace(role="model", parts=[]),
        )
    ]
    second = SimpleNamespace(text="status: ok\ntriples:\nA | likes | B | person | person\n")

    client, aio_models = _make_sequence_client([first, second])
    monkeypatch.setattr(
        "lethe.infra.gemini._build_gemini_client",
        lambda *_args, **_kwargs: client,
    )

    llm = GeminiLLM(Config(google_cloud_project="test-project"))
    result = await llm.dispatch(LLMRequest(system_prompt="", user_prompt="hello", max_tokens=64))

    assert result.startswith("status: ok")
    assert aio_models.call_count == 2


@pytest.mark.asyncio
async def test_dispatch_propagates_exception_from_generate(monkeypatch):
    """Exceptions from _generate must propagate out of dispatch, not be swallowed."""

    async def _raising_generate(*_args, **_kwargs):
        raise RuntimeError("simulated LLM error")

    monkeypatch.setattr(
        "lethe.infra.gemini._build_gemini_client",
        lambda *_args, **_kwargs: SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=_raising_generate))
        ),
    )

    llm = GeminiLLM(Config(google_cloud_project="test-project"))
    with pytest.raises(RuntimeError, match="simulated LLM error"):
        await llm.dispatch(LLMRequest(system_prompt="", user_prompt="hello"))


@pytest.mark.asyncio
async def test_dispatch_uses_new_gemini_client_builder(monkeypatch: pytest.MonkeyPatch):
    fake_response = SimpleNamespace(text="ok")

    async def _fake_generate(*_args, **_kwargs):
        return fake_response

    fake_aio_models = SimpleNamespace(generate_content=_fake_generate)
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=fake_aio_models))
    build_client = MagicMock(return_value=fake_client)

    monkeypatch.setattr(gemini_mod, "_build_gemini_client", build_client, raising=False)

    llm = GeminiLLM(Config(google_cloud_project="test-project"))
    req = LLMRequest(system_prompt="system", user_prompt="hello", max_tokens=64)

    result = await llm.dispatch(req)

    assert result == "ok"
    build_client.assert_called_once()
