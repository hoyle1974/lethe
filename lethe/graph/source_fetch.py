from __future__ import annotations

import logging

from google.cloud import firestore

from lethe.config import Config
from lethe.constants import (
    NODE_TYPE_LOG,
    SOURCE_LOGS_MAX_PER_NODE,
    SOURCE_LOGS_MAX_TOTAL,
    TRAVERSE_BATCH_SIZE,
)
from lethe.graph.serialization import doc_to_node
from lethe.models.node import Node

log = logging.getLogger(__name__)


async def fetch_source_logs(
    entity_nodes: dict[str, Node],
    db: firestore.AsyncClient,
    config: Config,
    max_per_node: int = SOURCE_LOGS_MAX_PER_NODE,
    max_total: int = SOURCE_LOGS_MAX_TOTAL,
) -> dict[str, list[Node]]:
    """Fetch the most recent log nodes for each entity node by journal_entry_ids.

    Returns entity_uuid -> [log_node, ...], capped at max_per_node per entity
    and max_total log fetches total.
    """
    per_entity: dict[str, list[str]] = {}
    for uuid, node in entity_nodes.items():
        if node.node_type != NODE_TYPE_LOG and node.journal_entry_ids:
            per_entity[uuid] = node.journal_entry_ids[-max_per_node:]

    if not per_entity:
        return {}

    id_to_entities: dict[str, list[str]] = {}
    for entity_uuid, log_ids in per_entity.items():
        for log_id in log_ids:
            id_to_entities.setdefault(log_id, []).append(entity_uuid)

    fetch_ids = list(id_to_entities.keys())[:max_total]

    col = db.collection(config.lethe_collection)
    fetched: dict[str, Node] = {}
    for i in range(0, len(fetch_ids), TRAVERSE_BATCH_SIZE):
        chunk = fetch_ids[i : i + TRAVERSE_BATCH_SIZE]
        refs = [col.document(uid) for uid in chunk]
        async for snap in db.get_all(refs):
            if snap.exists:
                data = snap.to_dict() or {}
                node = doc_to_node(snap.id, data)
                if node.node_type == NODE_TYPE_LOG:
                    fetched[snap.id] = node

    log.info(
        "fetch_source_logs: fetched=%d log_nodes for %d entities",
        len(fetched),
        len(per_entity),
    )

    result: dict[str, list[Node]] = {}
    for entity_uuid, log_ids in per_entity.items():
        logs_for_entity = [fetched[lid] for lid in log_ids if lid in fetched]
        if logs_for_entity:
            result[entity_uuid] = logs_for_entity

    return result
