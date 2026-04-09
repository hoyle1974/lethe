import asyncio
import vertexai
from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput
from vertexai.generative_models import GenerativeModel, GenerationConfig
from lethe.config import Config
from lethe.infra.llm import LLMRequest

EMBED_TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
EMBED_TASK_QUERY = "RETRIEVAL_QUERY"


class GeminiEmbedder:
    def __init__(self, config: Config) -> None:
        vertexai.init(project=config.google_cloud_project, location=config.lethe_region)
        self._model = TextEmbeddingModel.from_pretrained(config.lethe_embedding_model)

    async def embed(self, text: str, task_type: str = EMBED_TASK_DOCUMENT) -> list[float]:
        inputs = [TextEmbeddingInput(text, task_type)]
        results = await asyncio.to_thread(self._model.get_embeddings, inputs)
        return list(results[0].values)

    async def embed_batch(
        self, texts: list[str], task_type: str = EMBED_TASK_DOCUMENT
    ) -> list[list[float]]:
        inputs = [TextEmbeddingInput(t, task_type) for t in texts]
        results = await asyncio.to_thread(self._model.get_embeddings, inputs)
        return [list(r.values) for r in results]


class GeminiLLM:
    def __init__(self, config: Config) -> None:
        vertexai.init(project=config.google_cloud_project, location=config.lethe_region)
        self._model_name = config.lethe_llm_model

    async def dispatch(self, req: LLMRequest) -> str:
        model = GenerativeModel(
            self._model_name,
            system_instruction=req.system_prompt if req.system_prompt else None,
        )
        gen_cfg = GenerationConfig(max_output_tokens=req.max_tokens)
        response = await asyncio.to_thread(
            model.generate_content,
            req.user_prompt,
            generation_config=gen_cfg,
        )
        return response.text
