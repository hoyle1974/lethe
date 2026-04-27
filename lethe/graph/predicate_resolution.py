from __future__ import annotations

import logging
import os

from jinja2 import BaseLoader, Environment

from lethe.constants import LLM_MAX_TOKENS_PREDICATE_RESOLUTION
from lethe.infra.llm import LLMDispatcher, LLMRequest

log = logging.getLogger(__name__)

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
_RESOLUTION_TEMPLATE = None

RESOLUTION_SYSTEM = "You are a knowledge graph ontology guardian. Follow the output format exactly."


def _get_resolution_template():
    global _RESOLUTION_TEMPLATE
    if _RESOLUTION_TEMPLATE is None:
        path = os.path.join(_PROMPT_DIR, "predicate_resolution.txt")
        with open(path) as f:
            source = f.read()
        _RESOLUTION_TEMPLATE = Environment(loader=BaseLoader()).from_string(source)
    return _RESOLUTION_TEMPLATE


def _render_prompt(proposed: str, subject: str, object_: str, existing: list[str]) -> str:
    return _get_resolution_template().render(
        proposed=proposed,
        subject=subject,
        object=object_,
        existing_predicates=", ".join(existing),
    )


def _parse_response(text: str, existing: list[str], proposed: str) -> str:
    """Return the resolved predicate. Falls back to proposed on any parse failure."""
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if line.upper().startswith("EXISTING:"):
        candidate = line.split(":", 1)[1].strip().lower()
        if not candidate:
            log.warning(
                "predicate_resolution: LLM returned empty existing predicate — using proposed %r",
                proposed,
            )
            return proposed
        if candidate in existing:
            return candidate
        log.warning(
            "predicate_resolution: LLM returned unknown existing predicate %r — using proposed %r",
            candidate,
            proposed,
        )
        return proposed
    return proposed


async def resolve_new_predicate(
    llm: LLMDispatcher,
    proposed: str,
    triple_subject: str,
    triple_object: str,
    existing: list[str],
) -> str:
    """
    Ask the LLM whether `proposed` maps to an existing predicate or is genuinely novel.

    Returns an existing predicate name if the LLM redirects, otherwise returns `proposed`.
    Falls back to `proposed` on any error so ingestion never stalls.
    """
    if not existing:
        return proposed
    try:
        user_prompt = _render_prompt(proposed, triple_subject, triple_object, existing)
        response = await llm.dispatch(
            LLMRequest(
                system_prompt=RESOLUTION_SYSTEM,
                user_prompt=user_prompt,
                max_tokens=LLM_MAX_TOKENS_PREDICATE_RESOLUTION,
            )
        )
        return _parse_response(response, existing, proposed)
    except Exception as exc:
        log.warning("predicate_resolution: LLM error, using proposed predicate: %s", exc)
        return proposed
