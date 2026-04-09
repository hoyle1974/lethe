from __future__ import annotations
import os
import re
from dataclasses import dataclass

from jinja2 import Environment, BaseLoader

from lethe.infra.llm import LLMDispatcher, LLMRequest
from lethe.graph.ensure_node import normalized_predicate

_PRONOUNS = {
    "i", "me", "my", "myself", "mine",
    "we", "us", "our", "ours", "ourselves",
    "you", "your", "yours", "yourself", "yourselves",
}

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
_REFINERY_TEMPLATE = None


def _get_refinery_template():
    global _REFINERY_TEMPLATE
    if _REFINERY_TEMPLATE is None:
        path = os.path.join(_PROMPT_DIR, "refinery.txt")
        with open(path) as f:
            source = f.read()
        _REFINERY_TEMPLATE = Environment(loader=BaseLoader()).from_string(source)
    return _REFINERY_TEMPLATE


@dataclass
class RefineryTriple:
    subject: str
    predicate: str
    object: str
    subject_type: str = "generic"
    object_type: str = "generic"
    is_new_predicate: bool = False
    canonical_predicate: str = ""

    def __post_init__(self):
        if self.predicate.upper().startswith("NEW:"):
            self.is_new_predicate = True
            self.canonical_predicate = normalized_predicate(self.predicate[4:].strip())
        else:
            self.canonical_predicate = normalized_predicate(self.predicate)


def resolve_pronoun(term: str, owner_name: str = "") -> str | None:
    """Return resolved term, or None if it's an unresolvable pronoun."""
    if term.lower() in _PRONOUNS:
        return owner_name if owner_name else None
    return term


def parse_refinery_output(raw: str) -> tuple[str, list[RefineryTriple]]:
    """Parse key/value LLM output into (status, triples)."""
    lines = [line.strip() for line in raw.strip().splitlines()]
    status = "none"
    triples: list[RefineryTriple] = []
    in_triples = False

    for line in lines:
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("status:"):
            status = line.split(":", 1)[1].strip().lower()
            continue
        if lower == "triples:":
            in_triples = True
            continue
        if in_triples:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) not in (3, 5):
                continue
            subject, predicate, obj = parts[0], parts[1], parts[2]
            if not subject or not predicate or not obj:
                continue
            sub_type = parts[3] if len(parts) == 5 else "generic"
            obj_type = parts[4] if len(parts) == 5 else "generic"
            triples.append(RefineryTriple(
                subject=subject,
                predicate=predicate,
                object=obj,
                subject_type=sub_type,
                object_type=obj_type,
            ))

    return status, triples


def build_refinery_prompt(
    node_types: list[str],
    allowed_predicates: list[str],
    text: str,
    owner_name: str = "",
) -> str:
    tmpl = _get_refinery_template()
    return tmpl.render(
        node_types=", ".join(node_types),
        allowed_predicates=", ".join(allowed_predicates),
        text=text,
        owner_name=owner_name,
    )


REFINERY_SYSTEM = "You are a knowledge graph extraction engine. Follow the output format exactly."


async def extract_triples(
    llm: LLMDispatcher,
    text: str,
    node_types: list[str],
    allowed_predicates: list[str],
    owner_name: str = "",
) -> tuple[str, list[RefineryTriple]]:
    prompt = build_refinery_prompt(node_types, allowed_predicates, text, owner_name)
    raw = await llm.dispatch(LLMRequest(
        system_prompt=REFINERY_SYSTEM,
        user_prompt=prompt,
        max_tokens=512,
    ))
    return parse_refinery_output(raw)
