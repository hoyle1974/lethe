from __future__ import annotations
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

from lethe.config import Config
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import Vector, DistanceMeasure, FieldFilter
from lethe.models.node import Node

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


def parse_to_utc(value: object) -> Optional[datetime]:
    """Normalize Firestore / ISO timestamps to timezone-aware UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            s = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
    ts_fn = getattr(value, "timestamp", None)
    if callable(ts_fn):
        try:
            return datetime.fromtimestamp(float(ts_fn()), tz=timezone.utc)
        except (TypeError, OSError, ValueError):
            pass
    return None


def half_life_days_for_node_type(node_type: str) -> float:
    if node_type == "log":
        return 30.0
    if node_type in ("entity", "relationship"):
        return 365.0
    return 365.0


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


def doc_to_node(doc_id: str, data: dict) -> Node:
    data.pop("vector_distance", None)
    updated_at = parse_to_utc(data.get("updated_at"))
    created_at = parse_to_utc(data.get("created_at"))
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
        created_at=created_at,
        updated_at=updated_at,
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
) -> list[tuple[Node, float]]:
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
    pool = min(max(limit * 5, limit), _SEARCH_POOL_MAX)
    scored = await vector_search(
        db, config, query_vector, node_types, domain, user_id, pool
    )
    now_utc = datetime.now(timezone.utc)
    decorated = [
        (node, effective_distance_decay(node, raw_distance, now_utc))
        for node, raw_distance in scored
    ]
    decorated.sort(key=lambda x: x[1])
    ordered = [n for n, _ in decorated]
    if min_significance > 0.0:
        ordered = [n for n in ordered if n.weight >= min_significance]
    result = ordered[:limit]
    log.info("execute_search: query=%r vec=%d returned=%d", query, len(scored), len(result))
    return result
