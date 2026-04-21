from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from jinja2 import BaseLoader, Environment

from lethe.constants import (
    DEFAULT_NODE_TYPE,
    LLM_MAX_TOKENS_EXTRACTION,
)
from lethe.graph.ensure_node import normalized_predicate
from lethe.infra.llm import LLMDispatcher, LLMRequest

log = logging.getLogger(__name__)

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
    subject_type: str = DEFAULT_NODE_TYPE
    object_type: str = DEFAULT_NODE_TYPE
    is_new_predicate: bool = False
    canonical_predicate: str = ""

    def __post_init__(self):
        if self.predicate.upper().startswith("NEW:"):
            self.is_new_predicate = True
            self.canonical_predicate = normalized_predicate(self.predicate[4:].strip())
        else:
            self.canonical_predicate = normalized_predicate(self.predicate)


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
            sub_type = parts[3] if len(parts) == 5 else DEFAULT_NODE_TYPE
            obj_type = parts[4] if len(parts) == 5 else DEFAULT_NODE_TYPE
            triples.append(
                RefineryTriple(
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    subject_type=sub_type,
                    object_type=obj_type,
                )
            )

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
    log.info("extraction: sending prompt to LLM (text=%r)", text[:120])
    raw = await llm.dispatch(
        LLMRequest(
            system_prompt=REFINERY_SYSTEM,
            user_prompt=prompt,
            max_tokens=LLM_MAX_TOKENS_EXTRACTION,
        )
    )
    log.info("extraction: raw LLM response:\n%s", raw)
    status, triples = parse_refinery_output(raw)
    log.info("extraction: parsed status=%r triples=%d", status, len(triples))
    for t in triples:
        log.info(
            "extraction: triple %r | %r | %r (sub_type=%r obj_type=%r new=%s)",
            t.subject,
            t.predicate,
            t.object,
            t.subject_type,
            t.object_type,
            t.is_new_predicate,
        )
    return status, triples
