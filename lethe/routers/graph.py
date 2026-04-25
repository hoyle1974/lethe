from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Depends
from google.cloud import firestore

from lethe.config import Config
from lethe.constants import (
    LLM_MAX_TOKENS_GRAPH_SUMMARY_DRAFT,
    LLM_MAX_TOKENS_GRAPH_SUMMARY_FINAL,
    LLM_MAX_TOKENS_GRAPH_SUMMARY_THOUGHT,
)
from lethe.deps import get_config, get_db, get_embedder, get_llm
from lethe.graph.search import execute_search
from lethe.graph.source_fetch import fetch_source_logs
from lethe.graph.traverse import graph_expand
from lethe.infra.embedder import Embedder
from lethe.infra.llm import LLMDispatcher, LLMRequest
from lethe.models.node import GraphExpandRequest, GraphExpandResponse, GraphSummarizeResponse

router = APIRouter()
log = logging.getLogger(__name__)
_BULLET_PREFIX = re.compile(r"^\s*(?:[-*]\s+|\d+[.)]\s+)")


def _extract_target_queries(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text or text.upper() == "NONE":
        return []
    candidates: list[str] = []
    chunks = re.split(r"[\n,;]+", text)
    for chunk in chunks:
        normalized = _BULLET_PREFIX.sub("", chunk).strip(" .")
        if not normalized:
            continue
        if normalized.upper() == "NONE":
            continue
        if normalized not in candidates:
            candidates.append(normalized)
    return candidates[:3]


def _safe_query(q: str) -> str:
    return f"<query>{q}</query>"


def _is_broad_query(query: str) -> bool:
    parts = [p for p in re.split(r"\s+", (query or "").strip()) if p]
    return len(parts) <= 2


def _is_question_query(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return False
    if "?" in q:
        return True
    question_starts = (
        "who",
        "what",
        "when",
        "where",
        "why",
        "how",
        "which",
        "whom",
        "whose",
        "is ",
        "are ",
        "do ",
        "does ",
        "did ",
        "can ",
        "could ",
        "should ",
        "would ",
    )
    return q.startswith(question_starts)


def _merge_graphs(base: GraphExpandResponse, extra: GraphExpandResponse) -> GraphExpandResponse:
    merged_nodes = dict(base.nodes)
    merged_nodes.update(extra.nodes)
    merged_edges = list(base.edges)
    seen_edges = {(e.subject_uuid, e.predicate, e.object_uuid) for e in merged_edges}
    for edge in extra.edges:
        key = (edge.subject_uuid, edge.predicate, edge.object_uuid)
        if key not in seen_edges:
            seen_edges.add(key)
            merged_edges.append(edge)
    return GraphExpandResponse(nodes=merged_nodes, edges=merged_edges)


@router.post("/v1/graph/expand", response_model=GraphExpandResponse)
async def expand(
    req: GraphExpandRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    config: Config = Depends(get_config),
) -> GraphExpandResponse:
    return await graph_expand(
        db=db,
        embedder=embedder,
        config=config,
        seed_ids=req.seed_ids,
        query=req.query,
        hops=req.hops,
        limit_per_edge=req.limit_per_edge,
        self_seed_neighbor_floor=req.self_seed_neighbor_floor,
        user_id=req.user_id,
    )


@router.post(
    "/v1/graph/summarize",
    response_model=GraphSummarizeResponse,
    response_model_exclude_none=True,
)
async def summarize(
    req: GraphExpandRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    config: Config = Depends(get_config),
    llm: LLMDispatcher = Depends(get_llm),
) -> GraphSummarizeResponse:
    log.info(
        "summarize:start seeds=%d hops=%d limit_per_edge=%d user_id=%s query_len=%d",
        len(req.seed_ids),
        req.hops,
        req.limit_per_edge,
        req.user_id,
        len((req.query or "").strip()),
    )
    broad_query_mode = _is_broad_query(req.query or "")
    question_query_mode = _is_question_query(req.query or "")
    expansion_query = None if broad_query_mode else req.query
    log.info(
        "summarize:query_mode broad=%s question=%s expansion_query=%s",
        broad_query_mode,
        question_query_mode,
        bool(expansion_query),
    )
    expanded = await graph_expand(
        db=db,
        embedder=embedder,
        config=config,
        seed_ids=req.seed_ids,
        query=expansion_query,
        hops=req.hops,
        limit_per_edge=req.limit_per_edge,
        self_seed_neighbor_floor=req.self_seed_neighbor_floor,
        user_id=req.user_id,
    )
    log.info(
        "summarize:pass1_graph nodes=%d edges=%d",
        len(expanded.nodes),
        len(expanded.edges),
    )
    q = req.query if req.query is not None else ""
    sq = _safe_query(q)
    if question_query_mode:
        system = (
            "You are a query-resolution RAG engine. "
            f"Resolve the user query using only graph evidence: {sq}. "
            "Return markdown with sections:\n"
            "Answer: direct response to the query.\n"
            "Evidence: bullet points with the most relevant supporting facts.\n"
            "Gaps: what is unknown or not supported by the retrieved graph.\n"
            "Do not use conversational filler."
        )
    elif broad_query_mode:
        system = (
            f"You are a knowledge summarization engine. The subject is: {sq}. "
            "Using ALL graph evidence provided, write a comprehensive profile summary. "
            "Cover every domain present in the graph: professional role, projects, tasks, "
            "relationships, personal life, home, family, pets, plans, "
            "and any other notable details.\n"
            "Do not omit topics just because they seem minor. Return markdown with sections:\n"
            "Profile: 2-3 sentence overview.\n"
            "Work & Projects: bullet points for professional facts, projects, tasks, deadlines.\n"
            "Relationships: bullet points for people, roles, and connections.\n"
            "Personal & Home: bullet points for personal life, family, pets, plans, errands.\n"
            "Open Items: pending tasks or unresolved questions from the graph.\n"
            "Do not use conversational filler. Be specific and complete."
        )
    else:
        system = (
            "You are a query-resolution RAG engine. Resolve this free-form query "
            f"using only graph evidence: {sq}. Return markdown with sections:\n"
            "Response: concise synthesis or recommended resolution grounded in facts.\n"
            "Evidence: bullet points with concrete supporting facts.\n"
            "Gaps: what is unknown, missing, or uncertain.\n"
            "Use short sections and bullets, not a single paragraph. "
            "Do not use conversational filler."
        )
    thought_system = (
        f"Query focus: {_safe_query(q)}. Based on the current graph data and this query, "
        "identify missing entities or relationships that would make this summary more complete? "
        "Reply with up to 3 short retrieval queries "
        "(entity names, relationship phrases, or topics), one per line, or 'NONE'. "
        "Keep each query tightly relevant to the user query; avoid tangential topics."
    )
    md = expanded.to_markdown(req.seed_ids)

    draft_summary, thought = await asyncio.gather(
        llm.dispatch(
            LLMRequest(
                system_prompt=system,
                user_prompt=md,
                max_tokens=LLM_MAX_TOKENS_GRAPH_SUMMARY_DRAFT,
            )
        ),
        llm.dispatch(
            LLMRequest(
                system_prompt=thought_system,
                user_prompt=md,
                max_tokens=LLM_MAX_TOKENS_GRAPH_SUMMARY_THOUGHT,
            )
        ),
    )
    log.info(
        "summarize:pass1_llm draft_chars=%d thought_chars=%d",
        len((draft_summary or "").strip()),
        len((thought or "").strip()),
    )

    target_queries = _extract_target_queries(thought or "")
    retrieval_seed_ids: list[str] = []
    if target_queries:
        search_results = await asyncio.gather(
            *[
                execute_search(
                    db=db,
                    embedder=embedder,
                    config=config,
                    query=target_query,
                    node_types=[],
                    domain=None,
                    user_id=req.user_id,
                    limit=5,
                    min_significance=0.0,
                )
                for target_query in target_queries
            ]
        )
        seen_seed_ids: set[str] = set()
        for (
            node_list,
            _edges,
        ) in search_results:  # edges discarded; BFS expansion finds them via traversal
            for node in node_list:
                if node.uuid not in seen_seed_ids:
                    seen_seed_ids.add(node.uuid)
                    retrieval_seed_ids.append(node.uuid)

    log.info(
        "summarize:thought_targets queries=%d retrieval_seed_ids=%d",
        len(target_queries),
        len(retrieval_seed_ids),
    )
    combined = expanded
    pass2_performed = False
    if retrieval_seed_ids:
        pass2_performed = True
        extra = await graph_expand(
            db=db,
            embedder=embedder,
            config=config,
            seed_ids=retrieval_seed_ids,
            query=expansion_query,
            hops=1,
            limit_per_edge=req.limit_per_edge,
            self_seed_neighbor_floor=req.self_seed_neighbor_floor,
            user_id=req.user_id,
        )
        combined = _merge_graphs(combined, extra)
        log.info(
            "summarize:pass2_merged nodes=%d edges=%d",
            len(combined.nodes),
            len(combined.edges),
        )
    else:
        log.info("summarize:pass2_skipped no_retrieval_seed_ids")

    source_logs = await fetch_source_logs(
        entity_nodes=combined.nodes,
        db=db,
        config=config,
    )
    log.info(
        "summarize:source_logs entities_with_logs=%d",
        len(source_logs),
    )
    final_md = combined.to_markdown(req.seed_ids, source_logs=source_logs)
    if question_query_mode:
        final_prompt = (
            "Improve this draft using the enriched graph. Return sections:\n"
            "Answer:\nEvidence:\nGaps:\n"
            "Preserve factual accuracy and avoid filler.\n\n"
            f"Draft Summary:\n{(draft_summary or '').strip()}\n\n"
            f"Enriched Graph:\n{final_md}"
        )
    elif broad_query_mode:
        final_prompt = (
            "Improve and expand this draft into a complete profile using the enriched graph. "
            "Include ALL topics present: work, projects, tasks, people, "
            "personal life, home, pets, plans. "
            "Return sections: Profile:\nWork & Projects:\nRelationships:\n"
            "Personal & Home:\nOpen Items:\n"
            "Preserve factual accuracy and avoid filler.\n\n"
            f"Draft Summary:\n{(draft_summary or '').strip()}\n\n"
            f"Enriched Graph:\n{final_md}"
        )
    else:
        final_prompt = (
            "Improve and expand this draft using the enriched graph. Return sections:\n"
            "Response:\nEvidence:\nGaps:\n"
            "Preserve factual accuracy and avoid filler.\n\n"
            f"Draft Summary:\n{(draft_summary or '').strip()}\n\n"
            f"Enriched Graph:\n{final_md}"
        )
    final_summary = await llm.dispatch(
        LLMRequest(
            system_prompt=system,
            user_prompt=final_prompt,
            max_tokens=LLM_MAX_TOKENS_GRAPH_SUMMARY_FINAL,
        )
    )
    final_summary_text = (final_summary or "").strip()
    if len(final_summary_text) < 100:
        if question_query_mode:
            retry_prompt = (
                "The previous answer was too brief. Rewrite with sections:\n"
                "Answer:\nEvidence:\nGaps:\n"
                "Use concrete supporting facts from the graph and explicitly note unknowns.\n\n"
                f"Query: {_safe_query(q)}\n\n"
                f"Graph:\n{final_md}"
            )
        elif broad_query_mode:
            retry_prompt = (
                "The previous response was too brief. Write a comprehensive profile with sections "
                "Profile, Work & Projects, Relationships, Personal & Home, and Open Items. "
                "Include ALL topics from the graph.\n\n"
                f"Subject: {_safe_query(q)}\n\n"
                f"Graph:\n{final_md}"
            )
        else:
            retry_prompt = (
                "The previous response was too brief. Rewrite as a compact markdown "
                "query-resolution brief with sections Response, Evidence, and Gaps.\n\n"
                f"Query: {_safe_query(q)}\n\n"
                f"Graph:\n{final_md}"
            )
        final_summary = await llm.dispatch(
            LLMRequest(
                system_prompt=system,
                user_prompt=retry_prompt,
                max_tokens=LLM_MAX_TOKENS_GRAPH_SUMMARY_FINAL,
            )
        )
        final_summary_text = (final_summary or "").strip()
    log.info(
        "summarize:done final_nodes=%d final_edges=%d summary_chars=%d",
        len(combined.nodes),
        len(combined.edges),
        len(final_summary_text),
    )
    debug_reasoning = None
    if req.debug:
        debug_reasoning = {
            "query": q,
            "broad_query_mode": broad_query_mode,
            "question_query_mode": question_query_mode,
            "seed_ids": req.seed_ids,
            "target_queries": target_queries,
            "retrieval_seed_ids": retrieval_seed_ids,
            "pass1": {
                "nodes": len(expanded.nodes),
                "edges": len(expanded.edges),
                "draft_summary_chars": len((draft_summary or "").strip()),
                "thought_response": (thought or "").strip(),
            },
            "pass2": {
                "performed": pass2_performed,
                "expanded_target_count": len(retrieval_seed_ids),
                "nodes": len(combined.nodes),
                "edges": len(combined.edges),
            },
            "final": {
                "summary_chars": len(final_summary_text),
            },
        }

    return GraphSummarizeResponse(
        summary=final_summary_text,
        debug_reasoning=debug_reasoning,
    )
