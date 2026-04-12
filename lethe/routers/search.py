from __future__ import annotations

from fastapi import APIRouter, Depends
from google.cloud import firestore

from lethe.config import Config
from lethe.deps import get_config, get_db, get_embedder
from lethe.graph.search import execute_search
from lethe.infra.embedder import Embedder
from lethe.models.node import SearchRequest, SearchResponse

router = APIRouter()


@router.post("/v1/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    config: Config = Depends(get_config),
) -> SearchResponse:
    nodes, edges = await execute_search(
        db=db,
        embedder=embedder,
        config=config,
        query=req.query,
        node_types=req.node_types or [],
        domain=req.domain,
        user_id=req.user_id,
        limit=req.limit,
        min_significance=req.min_significance,
    )
    return SearchResponse(nodes=nodes, edges=edges)
