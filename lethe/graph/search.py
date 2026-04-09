from __future__ import annotations
import logging
import math
from typing import Optional

from google.cloud import firestore

from lethe.config import Config
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import Vector, DistanceMeasure, FieldFilter
from lethe.models.node import Node

log = logging.getLogger(__name__)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def doc_to_node(doc_id: str, data: dict) -> Node:
    data.pop("vector_distance", None)
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

    filters = [FieldFilter("user_id", "==", user_id)]
    if node_types:
        filters.append(FieldFilter("node_type", "in", node_types))
    else:
        filters.append(FieldFilter("node_type", "!=", "log"))
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
        )
        results: list[Node] = []
        async for doc in vq.stream():
            data = doc.to_dict() or {}
            results.append(doc_to_node(doc.id, data))
        log.info("vector_search: %d results for user_id=%s", len(results), user_id)
        return results
    except Exception as e:
        log.warning("vector_search failed: %s", e)
        return []


async def execute_search(
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
    results = await vector_search(db, config, query_vector, node_types, domain, user_id, limit)
    if min_significance > 0.0:
        results = [n for n in results if n.weight >= min_significance]
    result = results[:limit]
    log.info("execute_search: query=%r vec=%d returned=%d", query, len(results), len(result))
    return result
