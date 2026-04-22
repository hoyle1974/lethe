from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from google.cloud import firestore

from lethe.config import Config
from lethe.constants import DEFAULT_USER_ID
from lethe.deps import get_config, get_db
from lethe.graph.serialization import doc_to_node
from lethe.models.node import Node

router = APIRouter()


@router.get("/v1/nodes/{uuid}", response_model=Node)
async def get_node(
    uuid: str,
    db: firestore.AsyncClient = Depends(get_db),
    config: Config = Depends(get_config),
) -> Node:
    snap = await db.collection(config.lethe_collection).document(uuid).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="node not found")
    return doc_to_node(snap.id, snap.to_dict() or {})


@router.get("/v1/nodes", response_model=list[Node])
async def list_nodes(
    node_type: Optional[str] = Query(default=None),
    domain: Optional[str] = Query(default=None),
    user_id: str = Query(default=DEFAULT_USER_ID),
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: firestore.AsyncClient = Depends(get_db),
    config: Config = Depends(get_config),
) -> list[Node]:
    from lethe.infra.fs_helpers import FieldFilter

    col = db.collection(config.lethe_collection)
    q = col.where(filter=FieldFilter("user_id", "==", user_id))
    if node_type:
        q = q.where(filter=FieldFilter("node_type", "==", node_type))
    if domain:
        q = q.where(filter=FieldFilter("domain", "==", domain))
    q = q.limit(limit + offset)

    all_results: list[Node] = []
    async for doc in q.stream():
        data = doc.to_dict() or {}
        all_results.append(doc_to_node(doc.id, data))

    all_results.sort(key=lambda n: n.created_at or "", reverse=False)
    return all_results[offset : offset + limit]
