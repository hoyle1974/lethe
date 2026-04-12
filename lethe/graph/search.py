from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

from lethe.config import Config
from lethe.constants import (
    EDGE_HALF_LIFE_DAYS,
    EMBEDDING_TASK_RETRIEVAL_QUERY,
    LOG_NODE_HALF_LIFE_DAYS,
    NODE_TYPE_LOG,
    STRUCTURED_NODE_HALF_LIFE_DAYS,
)
from lethe.graph.ensure_node import doc_to_edge, doc_to_node
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import DistanceMeasure, FieldFilter, Vector
from lethe.models.node import Edge, Node

log = logging.getLogger(__name__)

_REINFORCEMENT_ALPHA = 0.05
_REINFORCEMENT_MAX_ENTRIES = 50
_SEARCH_POOL_MAX = 200


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def half_life_days_for_node_type(node_type: str) -> float:
    if node_type == NODE_TYPE_LOG:
        return LOG_NODE_HALF_LIFE_DAYS
    return STRUCTURED_NODE_HALF_LIFE_DAYS


def effective_distance_decay(
    node: Node,
    raw_distance: float,
    now_utc: datetime,
    reinforcement_alpha: float = _REINFORCEMENT_ALPHA,
) -> float:
    """Lower is better (cosine distance). Applies half-life decay and reinforcement offset."""
    ref = node.updated_at or node.created_at
    if ref is None:
        age_days = 0.0
    else:
        age_days = max(0.0, (now_utc - ref).total_seconds() / 86400.0)
    hl = half_life_days_for_node_type(node.node_type)
    decay_factor = 0.5 ** (age_days / hl) if hl > 0 else 1.0
    n_entries = min(len(node.journal_entry_ids), _REINFORCEMENT_MAX_ENTRIES)
    reinforcement = 1.0 + reinforcement_alpha * n_entries
    denom = decay_factor * reinforcement
    if denom <= 0.0:
        return raw_distance
    return raw_distance / denom


async def vector_search(
    db: firestore.AsyncClient,
    config: Config,
    query_vector: list[float],
    node_types: list[str],
    domain: Optional[str],
    user_id: str,
    limit: int,
) -> list[tuple[Node, float]]:
    col = db.collection(config.lethe_collection)

    filters = [FieldFilter("user_id", "==", user_id)]
    if node_types:
        filters.append(FieldFilter("node_type", "in", node_types))
    else:
        filters.append(FieldFilter("node_type", "!=", NODE_TYPE_LOG))
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
            distance_result_field="vector_distance",
            limit=limit,
        )
        results: list[tuple[Node, float]] = []
        async for doc in vq.stream():
            data = doc.to_dict() or {}
            dist_raw = data.get("vector_distance", 1.0)
            try:
                raw_distance = float(dist_raw)
            except (TypeError, ValueError):
                raw_distance = 1.0
            results.append((doc_to_node(doc.id, data), raw_distance))
        log.info("vector_search: %d results for user_id=%s", len(results), user_id)
        return results
    except Exception as e:
        log.warning("vector_search failed: %s", e)
        return []


async def _edge_vector_search(
    db: firestore.AsyncClient,
    config: Config,
    query_vector: list[float],
    domain: Optional[str],
    user_id: str,
    limit: int,
) -> list[tuple[Edge, float]]:
    col = db.collection(config.lethe_relationships_collection)

    filters = [FieldFilter("user_id", "==", user_id)]
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
            distance_result_field="vector_distance",
            limit=limit,
        )
        results: list[tuple[Edge, float]] = []
        async for doc in vq.stream():
            data = doc.to_dict() or {}
            dist_raw = data.get("vector_distance", 1.0)
            try:
                raw_distance = float(dist_raw)
            except (TypeError, ValueError):
                raw_distance = 1.0
            results.append((doc_to_edge(doc.id, data), raw_distance))
        log.info("_edge_vector_search: %d results for user_id=%s", len(results), user_id)
        return results
    except Exception as e:
        log.warning("_edge_vector_search failed: %s", e)
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
) -> tuple[list[Node], list[Edge]]:
    query_vector = await embedder.embed(query, EMBEDDING_TASK_RETRIEVAL_QUERY)
    pool = min(max(limit * 5, limit), _SEARCH_POOL_MAX)

    node_scored, edge_scored = await asyncio.gather(
        vector_search(db, config, query_vector, node_types, domain, user_id, pool),
        _edge_vector_search(db, config, query_vector, domain, user_id, pool),
    )

    now_utc = datetime.now(timezone.utc)

    # Rank nodes with temporal decay
    decorated_nodes = [(n, effective_distance_decay(n, d, now_utc)) for n, d in node_scored]
    decorated_nodes.sort(key=lambda x: x[1])
    nodes = [n for n, _ in decorated_nodes if n.weight > 0.0]
    if min_significance > 0.0:
        nodes = [n for n in nodes if n.weight >= min_significance]
    nodes = nodes[:limit]

    # Rank edges with temporal decay using EDGE_HALF_LIFE_DAYS
    decorated_edges: list[tuple[Edge, float]] = []
    for edge, raw in edge_scored:
        ref = edge.updated_at or edge.created_at
        age_days = max(0.0, (now_utc - ref).total_seconds() / 86400.0) if ref else 0.0
        decay = 0.5 ** (age_days / EDGE_HALF_LIFE_DAYS) if EDGE_HALF_LIFE_DAYS > 0 else 1.0
        n_entries = min(len(edge.journal_entry_ids), _REINFORCEMENT_MAX_ENTRIES)
        reinforcement = 1.0 + _REINFORCEMENT_ALPHA * n_entries
        denom = decay * reinforcement
        effective = raw / denom if denom > 0 else raw
        decorated_edges.append((edge, effective))
    decorated_edges.sort(key=lambda x: x[1])
    edges = [e for e, _ in decorated_edges if e.weight > 0.0]
    edges = edges[:limit]

    log.info("execute_search: query=%r nodes=%d edges=%d", query, len(nodes), len(edges))
    return nodes, edges
