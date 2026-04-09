import asyncio
import logging
import vertexai
from lethe.config import Config
from lethe.constants import EMBEDDING_TASK_RETRIEVAL_DOCUMENT
from lethe.infra.llm import LLMRequest
from lethe.types import EmbeddingTaskType
from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:  # pragma: no cover - optional dependency in test environments
    genai = None
    genai_types = None

log = logging.getLogger(__name__)


def _build_gemini_client(project: str, location: str):
    if genai is None:
        raise RuntimeError(
            "google-genai is required for GeminiLLM. Install dependencies from requirements.txt."
        )
    return genai.Client(vertexai=True, project=project, location=location)


class GenerativeModel:
    """Compatibility shim for tests expecting a GenerativeModel symbol."""

    def __init__(self, model_name: str, *, project: str, location: str, system_instruction: str | None):
        self._model_name = model_name
        self._project = project
        self._location = location
        self._system_instruction = system_instruction

    def generate_content(self, user_prompt: str, *, generation_config: object):
        client = _build_gemini_client(project=self._project, location=self._location)
        return client.models.generate_content(
            model=self._model_name,
            contents=user_prompt,
            config=generation_config,
        )

class GeminiEmbedder:
    def __init__(self, config: Config) -> None:
        vertexai.init(project=config.google_cloud_project, location=config.lethe_region)
        self._model = TextEmbeddingModel.from_pretrained(config.lethe_embedding_model)

    async def embed(
        self,
        text: str,
        task_type: EmbeddingTaskType = EMBEDDING_TASK_RETRIEVAL_DOCUMENT,
    ) -> list[float]:
        inputs = [TextEmbeddingInput(text, task_type)]
        results = await asyncio.to_thread(self._model.get_embeddings, inputs)
        return list(results[0].values)

    async def embed_batch(
        self,
        texts: list[str],
        task_type: EmbeddingTaskType = EMBEDDING_TASK_RETRIEVAL_DOCUMENT,
    ) -> list[list[float]]:
        inputs = [TextEmbeddingInput(t, task_type) for t in texts]
        results = await asyncio.to_thread(self._model.get_embeddings, inputs)
        return [list(r.values) for r in results]


class GeminiLLM:
    def __init__(self, config: Config) -> None:
        self._project = config.google_cloud_project
        self._location = config.lethe_region
        self._model_name = config.lethe_llm_model

    async def dispatch(self, req: LLMRequest) -> str:
        config_kwargs: dict[str, object] = {"max_output_tokens": req.max_tokens}
        if req.system_prompt:
            config_kwargs["system_instruction"] = req.system_prompt

        generation_config: object = config_kwargs
        if genai_types is not None and hasattr(genai_types, "GenerateContentConfig"):
            generation_config = genai_types.GenerateContentConfig(**config_kwargs)

        try:
            model = GenerativeModel(
                self._model_name,
                project=self._project,
                location=self._location,
                system_instruction=req.system_prompt if req.system_prompt else None,
            )
            response = await asyncio.to_thread(
                model.generate_content,
                req.user_prompt,
                generation_config=generation_config,
            )
            text = self._extract_response_text(response)
            if text:
                return text

            finish_reason = self._first_finish_reason(response)
            if finish_reason == "MAX_TOKENS":
                log.warning(
                    "GeminiLLM.dispatch response truncated at max tokens before text output."
                )
            else:
                log.warning(
                    "GeminiLLM.dispatch generation returned empty candidate content."
                )
            return "status: none\ntriples:\n"
        except Exception as e:
            log.warning("GeminiLLM.dispatch generation failed (likely safety filter): %s", e)
            return "status: none\ntriples:\n"

    def _extract_response_text(self, response: object) -> str | None:
        try:
            text = getattr(response, "text", None)
            if isinstance(text, str) and text.strip():
                return text
        except Exception:
            # Fall through to candidate parsing below.
            pass

        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            text_parts = [part.text for part in parts if getattr(part, "text", None)]
            if text_parts:
                return "".join(text_parts)
        return None

    def _first_finish_reason(self, response: object) -> str | None:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return None
        finish_reason = getattr(candidates[0], "finish_reason", None)
        return str(finish_reason) if finish_reason is not None else None
