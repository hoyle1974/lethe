from __future__ import annotations

from fastapi import APIRouter, Depends
from google.cloud import firestore

from lethe.config import Config
from lethe.deps import get_config, get_db, get_embedder
from lethe.graph.traverse import graph_expand
from lethe.infra.embedder import Embedder
from lethe.models.node import GraphExpandRequest, GraphExpandResponse

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
