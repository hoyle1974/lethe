from __future__ import annotations

import os

from lethe.constants import LLM_MAX_TOKENS_FACT_COLLISION
from lethe.infra.llm import LLMDispatcher, LLMRequest

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
_COLLISION_SYSTEM: str | None = None


def _get_collision_system() -> str:
    global _COLLISION_SYSTEM
    if _COLLISION_SYSTEM is None:
        with open(os.path.join(_PROMPT_DIR, "collision.txt")) as f:
            _COLLISION_SYSTEM = f.read()
    return _COLLISION_SYSTEM


async def evaluate_fact_collision(
    llm: LLMDispatcher,
    new_fact: str,
    existing_fact: str,
) -> str:
    """Return 'update' or 'insert'. Falls back to 'insert' on any error."""
    try:
        user_prompt = f"New Fact:\n{new_fact}\n\nExisting Fact:\n{existing_fact}"
        text = await llm.dispatch(
            LLMRequest(
                system_prompt=_get_collision_system(),
                user_prompt=user_prompt,
                max_tokens=LLM_MAX_TOKENS_FACT_COLLISION,
            )
        )
        if "update" in text.lower():
            return "update"
        return "insert"
    except Exception:
        return "insert"


async def evaluate_fact_collision_if_enabled(
    llm: LLMDispatcher,
    new_fact: str,
    existing_fact: str,
    enabled: bool = True,
) -> str:
    if not enabled:
        return "insert"
    return await evaluate_fact_collision(llm, new_fact, existing_fact)
