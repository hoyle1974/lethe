from __future__ import annotations

from fastapi import APIRouter, Depends
from google.cloud import firestore

from lethe.config import Config
from lethe.deps import get_config, get_db, get_embedder, get_llm
from lethe.graph.traverse import graph_expand
from lethe.infra.embedder import Embedder
from lethe.infra.llm import LLMDispatcher, LLMRequest
from lethe.models.node import GraphExpandRequest, GraphExpandResponse, GraphSummarizeResponse

router = APIRouter()


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
        user_id=req.user_id,
    )


@router.post("/v1/graph/summarize", response_model=GraphSummarizeResponse)
async def summarize(
    req: GraphExpandRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    config: Config = Depends(get_config),
    llm: LLMDispatcher = Depends(get_llm),
) -> GraphSummarizeResponse:
    expanded = await graph_expand(
        db=db,
        embedder=embedder,
        config=config,
        seed_ids=req.seed_ids,
        query=req.query,
        hops=req.hops,
        limit_per_edge=req.limit_per_edge,
        user_id=req.user_id,
    )
    q = req.query if req.query is not None else ""
    system = (
        "You are a memory summarization engine. Review the following knowledge graph extraction. "
        f"Write a single, dense paragraph summarizing the facts most relevant to the query: {q}. "
        "Do not use conversational filler."
    )
    md = expanded.to_markdown(req.seed_ids)
    text = await llm.dispatch(
        LLMRequest(system_prompt=system, user_prompt=md, max_tokens=2048)
    )
    return GraphSummarizeResponse(summary=(text or "").strip())
