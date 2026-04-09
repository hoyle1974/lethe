from typing import Protocol, runtime_checkable

from lethe.constants import EMBEDDING_TASK_RETRIEVAL_DOCUMENT
from lethe.types import EmbeddingTaskType


@runtime_checkable
class Embedder(Protocol):
    async def embed(
        self,
        text: str,
        task_type: EmbeddingTaskType = EMBEDDING_TASK_RETRIEVAL_DOCUMENT,
    ) -> list[float]:
        ...

    async def embed_batch(
        self,
        texts: list[str],
        task_type: EmbeddingTaskType = EMBEDDING_TASK_RETRIEVAL_DOCUMENT,
    ) -> list[list[float]]:
        ...
