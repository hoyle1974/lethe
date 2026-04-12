from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lethe.config import Config
from lethe.infra.gemini import GeminiLLM
from lethe.infra.llm import LLMRequest


@pytest.mark.asyncio
async def test_dispatch_uses_new_gemini_client_builder(monkeypatch: pytest.MonkeyPatch):
    fake_response = SimpleNamespace(text="ok")

    async def _fake_generate(*_args, **_kwargs):
        return fake_response

    fake_aio_models = SimpleNamespace(generate_content=_fake_generate)
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=fake_aio_models))
    build_client = MagicMock(return_value=fake_client)

    import lethe.infra.gemini as gemini_mod

    monkeypatch.setattr(gemini_mod, "_build_gemini_client", build_client, raising=False)

    llm = GeminiLLM(Config(google_cloud_project="test-project"))
    req = LLMRequest(system_prompt="system", user_prompt="hello", max_tokens=64)

    result = await llm.dispatch(req)

    assert result == "ok"
    build_client.assert_called_once()
