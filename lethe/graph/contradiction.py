from __future__ import annotations

import json
import logging
import re
from typing import Optional

from google.cloud import firestore

from lethe.constants import LLM_MAX_TOKENS_RELATIONSHIP_SUPERSEDES
from lethe.infra.llm import LLMDispatcher, LLMRequest

log = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


async def evaluate_relationship_supersedes(
    llm: LLMDispatcher,
    new_fact: str,
    existing_facts: list[tuple[str, str]],
) -> Optional[str]:
    """Return UUID of superseded relationship, or None."""
    if not existing_facts:
        return None
    lines = [f"- {uuid}: {text}" for uuid, text in existing_facts]
    system = (
        "You decide whether a new factual relationship contradicts or replaces an older one "
        "about the same subject (e.g. location change). "
        "If the new fact supersedes exactly one listed fact, respond with only that fact's UUID. "
        "Otherwise respond with exactly: none"
    )
    user = f"New Fact:\n{new_fact}\n\nExisting Facts:\n" + "\n".join(lines)
    try:
        text = await llm.dispatch(
            LLMRequest(
                system_prompt=system,
                user_prompt=user,
                max_tokens=LLM_MAX_TOKENS_RELATIONSHIP_SUPERSEDES,
            )
        )
    except Exception as e:
        log.warning("evaluate_relationship_supersedes LLM failed: %s", e)
        return None
    known = {uid.lower(): uid for uid, _ in existing_facts}
    for m in _UUID_RE.finditer(text or ""):
        cand = m.group(0).lower()
        if cand in known:
            return known[cand]
    return None


async def tombstone_relationship(
    db: firestore.AsyncClient,
    collection_name: str,
    old_rel_id: str,
    new_rel_id: str,
) -> None:
    ref = db.collection(collection_name).document(old_rel_id)
    snap = await ref.get()
    if not snap.exists:
        return
    data = snap.to_dict() or {}
    raw_meta = data.get("metadata", "{}")
    try:
        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else {}
    except json.JSONDecodeError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    meta["deprecated_by"] = new_rel_id
    await ref.update(
        {
            "weight": 0.0,
            "relevance_score": 0.0,
            "metadata": json.dumps(meta),
        }
    )
