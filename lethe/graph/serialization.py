from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from lethe.constants import (
    DEFAULT_DOMAIN,
    DEFAULT_NODE_TYPE,
    DEFAULT_RELATIONSHIP_WEIGHT,
    DEFAULT_USER_ID,
)
from lethe.models.node import Edge, Node

log = logging.getLogger(__name__)


def parse_to_utc(value: object) -> Optional[datetime]:
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


def doc_to_node(doc_id: str, data: dict) -> Node:
    data.pop("vector_distance", None)
    embedding = None
    raw_emb = data.get("embedding")
    if raw_emb is not None:
        try:
            embedding = list(raw_emb)
        except TypeError:
            embedding = None
    return Node(
        uuid=doc_id,
        node_type=data.get("node_type", DEFAULT_NODE_TYPE),
        content=data.get("content", ""),
        domain=data.get("domain", DEFAULT_DOMAIN),
        weight=float(data.get("weight", data.get("significance_weight", 0.5))),
        metadata=data.get("metadata", "{}"),
        journal_entry_ids=list(data.get("journal_entry_ids", [])),
        name_key=data.get("name_key"),
        user_id=data.get("user_id", DEFAULT_USER_ID),
        source=data.get("source"),
        created_at=parse_to_utc(data.get("created_at")),
        updated_at=parse_to_utc(data.get("updated_at")),
        embedding=embedding,
    )


def doc_to_edge(doc_id: str, data: dict) -> Edge:
    data.pop("vector_distance", None)
    subject_uuid = data.get("subject_uuid", "")
    predicate = data.get("predicate", "")
    object_uuid = data.get("object_uuid", "")
    if not subject_uuid or not predicate or not object_uuid:
        log.warning(
            "doc_to_edge: missing required fields for doc_id=%s "
            "(subject_uuid=%r predicate=%r object_uuid=%r)",
            doc_id,
            subject_uuid,
            predicate,
            object_uuid,
        )
    return Edge(
        uuid=doc_id,
        subject_uuid=subject_uuid,
        predicate=predicate,
        object_uuid=object_uuid,
        content=data.get("content", ""),
        weight=float(data.get("weight", DEFAULT_RELATIONSHIP_WEIGHT)),
        domain=data.get("domain", DEFAULT_DOMAIN),
        user_id=data.get("user_id", DEFAULT_USER_ID),
        source=data.get("source"),
        journal_entry_ids=list(data.get("journal_entry_ids", [])),
        created_at=parse_to_utc(data.get("created_at")),
        updated_at=parse_to_utc(data.get("updated_at")),
    )
