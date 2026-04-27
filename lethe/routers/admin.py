from __future__ import annotations

from fastapi import APIRouter, Depends
from google.cloud import firestore
from pydantic import BaseModel

from lethe.config import Config
from lethe.constants import (
    DEFAULT_USER_ID,
)
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


class StatsResponse(BaseModel):
    nodes_total: int
    nodes_by_type: dict[str, int]
    edges_total: int
    edges_by_predicate: dict[str, int]


router = APIRouter()


@router.get("/v1/health")
async def health():
    return {"status": "ok"}


@router.get("/v1/stats", response_model=StatsResponse)
async def stats(
    db: firestore.AsyncClient = Depends(get_db),
    config: Config = Depends(get_config),
) -> StatsResponse:
    nodes_by_type: dict[str, int] = {}
    async for doc in db.collection(config.lethe_collection).stream():
        nt = (doc.to_dict() or {}).get("node_type", "unknown")
        nodes_by_type[nt] = nodes_by_type.get(nt, 0) + 1

    edges_by_predicate: dict[str, int] = {}
    async for doc in db.collection(config.lethe_relationships_collection).stream():
        pred = (doc.to_dict() or {}).get("predicate", "unknown")
        edges_by_predicate[pred] = edges_by_predicate.get(pred, 0) + 1

    return StatsResponse(
        nodes_total=sum(nodes_by_type.values()),
        nodes_by_type=nodes_by_type,
        edges_total=sum(edges_by_predicate.values()),
        edges_by_predicate=edges_by_predicate,
    )


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


@router.post("/v1/admin/backfill", status_code=201)
async def backfill(
    req: BackfillRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    config: Config = Depends(get_config),
):
    col = db.collection(config.lethe_collection)
    # Collect docs needing embeddings
    pending: list[tuple[str, str]] = []  # (doc_id, content)
    async for doc in col.limit(req.limit * 10).stream():
        if len(pending) >= req.limit:
            break
        data = doc.to_dict() or {}
        if data.get("embedding") is not None:
            continue
        content = data.get("content", "")
        if not content:
            continue
        pending.append((doc.id, content))

    if not pending:
        return {"backfilled": 0}

    doc_ids, contents = zip(*pending)
    vectors = await embedder.embed_batch(list(contents))
    for doc_id, vector in zip(doc_ids, vectors):
        await col.document(doc_id).update({"embedding": Vector(vector)})
    return {"backfilled": len(pending)}


@router.post("/v1/admin/consolidate", response_model=ConsolidationResponse, status_code=201)
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
