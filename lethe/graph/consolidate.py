from __future__ import annotations

import logging
import re

from google.cloud import firestore
from pydantic import BaseModel, Field

from lethe.config import Config
from lethe.constants import (
    CONSOLIDATION_LOG_QUERY_LIMIT,
    DEFAULT_USER_ID,
    LLM_MAX_TOKENS_CONSOLIDATION,
    NODE_TYPE_LOG,
)
from lethe.graph.canonical_map import CanonicalMap
from lethe.graph.ingest import run_ingest
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import FieldFilter
from lethe.infra.llm import LLMDispatcher, LLMRequest
from lethe.models.node import IngestResponse

log = logging.getLogger(__name__)

_MAX_STATEMENTS = 3


class ConsolidationResponse(BaseModel):
    statements: list[str] = Field(default_factory=list)
    ingest_results: list[IngestResponse] = Field(default_factory=list)


def _parse_statements(llm_text: str) -> list[str]:
    """Extract up to 3 non-empty factual lines from LLM output."""
    if not llm_text:
        return []
    lines: list[str] = []
    for raw in llm_text.splitlines():
        s = raw.strip()
        if not s:
            continue
        s = re.sub(r"^\s*[-*•]\s*", "", s)
        s = re.sub(r"^\s*\d+\.\s*", "", s).strip()
        if not s:
            continue
        lines.append(s)
        if len(lines) >= _MAX_STATEMENTS:
            break
    return lines[:_MAX_STATEMENTS]


async def run_consolidation(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    canonical_map: CanonicalMap,
    user_id: str = DEFAULT_USER_ID,
) -> ConsolidationResponse:
    col = db.collection(config.lethe_collection)
    q = (
        col.where(filter=FieldFilter("user_id", "==", user_id))
        .where(filter=FieldFilter("node_type", "==", NODE_TYPE_LOG))
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(CONSOLIDATION_LOG_QUERY_LIMIT)
    )
    logs: list[str] = []
    try:
        async for doc in q.stream():
            data = doc.to_dict() or {}
            c = (data.get("content") or "").strip()
            if c:
                logs.append(c)
    except Exception as e:
        log.warning("run_consolidation: log query failed: %s", e)
        return ConsolidationResponse()

    if not logs:
        log.info("run_consolidation: no log nodes for user_id=%s", user_id)
        return ConsolidationResponse()

    combined = "\n\n---\n\n".join(logs)
    system = (
        "Identify overarching patterns, user preferences, or major life updates from these logs. "
        "Synthesize them into 1 to 3 core factual statements. "
        "Output plain text only: one statement per line, no numbering or bullets."
    )
    user_prompt = f"Recent journal logs:\n\n{combined}"
    try:
        llm_out = await llm.dispatch(
            LLMRequest(
                system_prompt=system,
                user_prompt=user_prompt,
                max_tokens=LLM_MAX_TOKENS_CONSOLIDATION,
            )
        )
    except Exception as e:
        log.warning("run_consolidation: LLM failed: %s", e)
        return ConsolidationResponse()

    statements = _parse_statements(llm_out or "")
    if not statements:
        return ConsolidationResponse()

    ingest_results: list[IngestResponse] = []
    for stmt in statements:
        try:
            resp = await run_ingest(
                db=db,
                embedder=embedder,
                llm=llm,
                config=config,
                canonical_map=canonical_map,
                text=stmt,
                domain="core_memory",
                user_id=user_id,
            )
            ingest_results.append(resp)
        except Exception as e:
            log.error("run_consolidation: ingest failed for %r: %s", stmt, e, exc_info=True)

    return ConsolidationResponse(statements=statements, ingest_results=ingest_results)
