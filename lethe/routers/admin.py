from __future__ import annotations

from fastapi import APIRouter, Depends
from google.cloud import firestore
from pydantic import BaseModel

from lethe.config import Config
from lethe.constants import DEFAULT_USER_ID
from lethe.deps import (
    get_canonical_map,
    get_config,
    get_db,
    get_embedder,
    get_llm,
)
from lethe.graph.canonical_map import CanonicalMap
from lethe.graph.consolidate import ConsolidationResponse, run_consolidation
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import Vector
from lethe.infra.llm import LLMDispatcher

router = APIRouter()


@router.get("/v1/health")
async def health():
    return {"status": "ok"}


@router.get("/v1/node-types")
async def node_types(canonical_map: CanonicalMap = Depends(get_canonical_map)):
    return {
        "node_types": canonical_map.node_types,
        "allowed_predicates": canonical_map.allowed_predicates,
    }


class BackfillRequest(BaseModel):
    limit: int = 100


class ConsolidateRequest(BaseModel):
    user_id: str = DEFAULT_USER_ID


@router.post("/v1/admin/backfill")
async def backfill(
    req: BackfillRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    config: Config = Depends(get_config),
):
    col = db.collection(config.lethe_collection)
    count = 0
    async for doc in col.limit(req.limit * 10).stream():
        data = doc.to_dict() or {}
        if data.get("embedding") is not None:
            continue
        content = data.get("content", "")
        if not content:
            continue
        vector = await embedder.embed(content)
        await col.document(doc.id).update({"embedding": Vector(vector)})
        count += 1
        if count >= req.limit:
            break
    return {"backfilled": count}


@router.post("/v1/admin/consolidate", response_model=ConsolidationResponse)
async def consolidate(
    req: ConsolidateRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    llm: LLMDispatcher = Depends(get_llm),
    config: Config = Depends(get_config),
    canonical_map: CanonicalMap = Depends(get_canonical_map),
) -> ConsolidationResponse:
    return await run_consolidation(
        db=db,
        embedder=embedder,
        llm=llm,
        config=config,
        canonical_map=canonical_map,
        user_id=req.user_id,
    )
