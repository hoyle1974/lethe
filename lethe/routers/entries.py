from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from google.cloud import firestore

from lethe.config import Config
from lethe.constants import DEFAULT_USER_ID, NODE_TYPE_LOG
from lethe.deps import get_config, get_db
from lethe.graph.search import doc_to_node
from lethe.models.node import Node

router = APIRouter()


@router.get("/v1/entries/{uuid}", response_model=Node)
async def get_entry(
    uuid: str,
    db: firestore.AsyncClient = Depends(get_db),
    config: Config = Depends(get_config),
) -> Node:
    snap = await db.collection(config.lethe_collection).document(uuid).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="entry not found")
    data = snap.to_dict() or {}
    if data.get("node_type") != NODE_TYPE_LOG:
        raise HTTPException(status_code=404, detail="entry not found")
    return doc_to_node(snap.id, data)


@router.get("/v1/entries", response_model=list[Node])
async def list_entries(
    user_id: str = Query(default=DEFAULT_USER_ID),
    limit: int = Query(default=20, ge=1, le=500),
    ascending: bool = Query(default=False),
    since: Optional[str] = Query(default=None),
    db: firestore.AsyncClient = Depends(get_db),
    config: Config = Depends(get_config),
) -> list[Node]:
    from lethe.infra.fs_helpers import FieldFilter

    col = db.collection(config.lethe_collection)
    # Filter only on user_id server-side; node_type, since, and sort are
    # handled client-side to avoid composite index requirements.
    q = col.where(filter=FieldFilter("user_id", "==", user_id)).limit(limit * 10)
    results: list[Node] = []
    async for doc in q.stream():
        data = doc.to_dict() or {}
        if data.get("node_type") != NODE_TYPE_LOG:
            continue
        if since and data.get("created_at", "") < since:
            continue
        results.append(doc_to_node(doc.id, data))

    results.sort(key=lambda n: n.created_at or "", reverse=not ascending)
    return results[:limit]
