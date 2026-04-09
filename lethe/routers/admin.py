from __future__ import annotations

from fastapi import APIRouter, Depends
from google.cloud import firestore
from pydantic import BaseModel

from lethe.config import Config
from lethe.deps import get_canonical_map, get_config, get_db, get_embedder
from lethe.graph.canonical_map import CanonicalMap
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import Vector

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
        vector = await embedder.embed(content, "RETRIEVAL_DOCUMENT")
        await col.document(doc.id).update({"embedding": Vector(vector)})
        count += 1
        if count >= req.limit:
            break
    return {"backfilled": count}
