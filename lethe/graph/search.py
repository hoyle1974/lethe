from __future__ import annotations
import asyncio
import math
from typing import Optional

from google.cloud import firestore

from lethe.config import Config
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import Vector, DistanceMeasure, FieldFilter
from lethe.models.node import Node


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def rrf_fuse(
    vector_results: list[Node],
    keyword_results: list[Node],
    k: int = 60,
) -> list[Node]:
    scores: dict[str, float] = {}
    by_uuid: dict[str, Node] = {}

    for rank, node in enumerate(vector_results):
        scores[node.uuid] = scores.get(node.uuid, 0.0) + 1.0 / (k + rank + 1)
        by_uuid[node.uuid] = node

    for rank, node in enumerate(keyword_results):
        scores[node.uuid] = scores.get(node.uuid, 0.0) + 1.0 / (k + rank + 1)
        by_uuid[node.uuid] = node

    ranked = sorted(scores.keys(), key=lambda uid: scores[uid], reverse=True)
    return [by_uuid[uid] for uid in ranked]


def doc_to_node(doc_id: str, data: dict) -> Node:
    data.pop("__vector_distance__", None)
    embedding = None
    raw = data.get("embedding")
    if raw is not None:
        try:
            embedding = list(raw)
        except TypeError:
            pass
    return Node(
        uuid=doc_id,
        node_type=data.get("node_type", "generic"),
        content=data.get("content", ""),
        domain=data.get("domain", "general"),
        weight=float(data.get("weight", data.get("significance_weight", 0.5))),
        metadata=data.get("metadata", "{}"),
        entity_links=list(data.get("entity_links", [])),
        predicate=data.get("predicate"),
        object_uuid=data.get("object_uuid"),
        subject_uuid=data.get("subject_uuid"),
        journal_entry_ids=list(data.get("journal_entry_ids", [])),
        name_key=data.get("name_key"),
        hot_edges=list(data.get("hot_edges", [])),
        relevance_score=data.get("relevance_score"),
        user_id=data.get("user_id", "global"),
        source=data.get("source"),
        embedding=embedding,
    )


async def vector_search(
    db: firestore.AsyncClient,
    config: Config,
    query_vector: list[float],
    node_types: list[str],
    domain: Optional[str],
    user_id: str,
    limit: int,
) -> list[Node]:
    col = db.collection(config.lethe_collection)

    # Build base query excluding log entries
    filters = [
        FieldFilter("user_id", "==", user_id),
        FieldFilter("node_type", "!=", "log"),
    ]
    if node_types:
        filters.append(FieldFilter("node_type", "in", node_types))
    if domain:
        filters.append(FieldFilter("domain", "==", domain))

    q = col
    for f in filters:
        q = q.where(filter=f)

    try:
        vq = q.find_nearest(
            vector_field="embedding",
            query_vector=Vector(query_vector),
            distance_measure=DistanceMeasure.COSINE,
            limit=limit,
            distance_result_field="__vector_distance__",
        )
        results: list[Node] = []
        async for doc in vq.stream():
            data = doc.to_dict() or {}
            results.append(doc_to_node(doc.id, data))
        return results
    except Exception:
        return []


async def keyword_search(
    db: firestore.AsyncClient,
    config: Config,
    keywords: str,
    node_types: list[str],
    domain: Optional[str],
    user_id: str,
    limit: int,
) -> list[Node]:
    col = db.collection(config.lethe_collection)
    q = (
        col
        .where(filter=FieldFilter("user_id", "==", user_id))
        .where(filter=FieldFilter("node_type", "!=", "log"))
        .limit(limit * 5)
    )
    results: list[Node] = []
    kw_lower = keywords.lower()
    try:
        async for doc in q.stream():
            data = doc.to_dict() or {}
            content = data.get("content", "").lower()
            if kw_lower not in content:
                continue
            if node_types and data.get("node_type") not in node_types:
                continue
            if domain and data.get("domain") != domain:
                continue
            results.append(doc_to_node(doc.id, data))
            if len(results) >= limit:
                break
    except Exception:
        pass
    return results


async def hybrid_search(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    query: str,
    node_types: list[str],
    domain: Optional[str],
    user_id: str,
    limit: int,
    min_significance: float,
) -> list[Node]:
    query_vector = await embedder.embed(query, "RETRIEVAL_QUERY")
    vec_results, kw_results = await asyncio.gather(
        vector_search(db, config, query_vector, node_types, domain, user_id, limit),
        keyword_search(db, config, query, node_types, domain, user_id, limit),
    )
    fused = rrf_fuse(vec_results, kw_results, k=config.lethe_rrf_k)
    if min_significance > 0.0:
        fused = [n for n in fused if n.weight >= min_significance]
    return fused[:limit]
