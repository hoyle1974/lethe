from __future__ import annotations

from typing import Literal, TypeAlias

EmbeddingTaskType: TypeAlias = Literal[
    "RETRIEVAL_DOCUMENT",
    "RETRIEVAL_QUERY",
]
