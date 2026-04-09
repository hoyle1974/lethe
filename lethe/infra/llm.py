from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class LLMRequest:
    system_prompt: str
    user_prompt: str
    max_tokens: int = 1024


@runtime_checkable
class LLMDispatcher(Protocol):
    async def dispatch(self, req: LLMRequest) -> str:
        ...
