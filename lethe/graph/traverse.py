from __future__ import annotations
import asyncio
import logging
from typing import Optional

from google.cloud import firestore

log = logging.getLogger(__name__)

from lethe.config import Config
from lethe.constants import (
    EMBEDDING_TASK_RETRIEVAL_QUERY,
    NODE_TYPE_LOG,
    TRAVERSAL_OBSERVATION_WEIGHT,
    TRAVERSAL_SIMILARITY_WEIGHT,
    TRAVERSE_BATCH_SIZE,
    TRAVERSE_NEIGHBOR_QUERY_LIMIT,
)
from lethe.graph.ensure_node import stable_self_id
from lethe.graph.search import cosine_similarity, doc_to_node
from lethe.infra.embedder import Embedder
from lethe.models.node import Node, Edge, GraphExpandResponse

def apply_self_seed_neighbor_floor(
    pruned: list[Node],
    self_neighbors: list[Node],
    query_vector: Optional[list[float]],
    floor: int,
    hop_idx: int,
    self_in_frontier: bool,
) -> list[Node]:
    """Keep a minimum number of first-hop SELF neighbors from being pruned."""
    if hop_idx != 0 or not self_in_frontier or floor <= 0:
        return pruned

    selected_self = prune_frontier_by_similarity(self_neighbors, query_vector, floor)
    existing = {n.uuid for n in pruned}
    merged = list(pruned)
    for node in selected_self:
        if node.uuid not in existing:
            merged.append(node)
            existing.add(node.uuid)
    return merged


def _is_alive(n: Node) -> bool:
    """False for tombstoned nodes (weight 0.0); True otherwise."""
    return n.weight > 0.0


def prune_frontier_by_similarity(
    nodes: list[Node],
    query_vector: Optional[list[float]],
    top_k: int,
) -> list[Node]:
    if len(nodes) <= top_k:
        return nodes
    max_observation_count = max(len(n.journal_entry_ids) for n in nodes) if nodes else 0
    scored = [
        (
            n,
            (
                (
                    cosine_similarity(n.embedding, query_vector)
                    if (query_vector is not None and n.embedding)
                    else 0.0
                ) * TRAVERSAL_SIMILARITY_WEIGHT
            )
            + (
                ((len(n.journal_entry_ids) / max_observation_count) if max_observation_count else 0.0)
                * TRAVERSAL_OBSERVATION_WEIGHT
            ),
        )
        for n in nodes
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    kept = [n.uuid for n, _ in scored[:top_k]]
    log.info(
        "prune_frontier: candidates=%d top_k=%d query=%s max_obs=%d kept=%s",
        len(nodes),
        top_k,
        bool(query_vector),
        max_observation_count,
        kept,
    )
    return [n for n, _ in scored[:top_k]]


async def _fetch_nodes_by_ids(
    db: firestore.AsyncClient,
    config: Config,
    ids: list[str],
) -> dict[str, Node]:
    col = db.collection(config.lethe_collection)
    result: dict[str, Node] = {}
    for i in range(0, len(ids), TRAVERSE_BATCH_SIZE):
        chunk = ids[i:i + TRAVERSE_BATCH_SIZE]
        refs = [col.document(uid) for uid in chunk]
        async for snap in db.get_all(refs):
            if snap.exists:
                data = snap.to_dict() or {}
                result[snap.id] = doc_to_node(snap.id, data)
    return result


async def _get_incoming_spo_edges(
    db: firestore.AsyncClient,
    config: Config,
    node_uuid: str,
    user_id: str,
) -> list[str]:
    """Return UUIDs of relationship nodes whose object_uuid == node_uuid."""
    from lethe.infra.fs_helpers import FieldFilter
    col = db.collection(config.lethe_collection)
    q = (
        col
        .where(filter=FieldFilter("object_uuid", "==", node_uuid))
        .where(filter=FieldFilter("user_id", "==", user_id))
        .limit(TRAVERSE_NEIGHBOR_QUERY_LIMIT)
    )
    ids: list[str] = []
    try:
        async for doc in q.stream():
            ids.append(doc.id)
    except Exception as e:
        log.warning("_get_incoming_spo_edges failed: %s", e)
    return ids


async def _get_nodes_linking_to(
    db: firestore.AsyncClient,
    config: Config,
    node_uuid: str,
    user_id: str,
) -> list[str]:
    """Return UUIDs of nodes that have node_uuid in their entity_links."""
    from lethe.infra.fs_helpers import FieldFilter
    col = db.collection(config.lethe_collection)
    q = (
        col
        .where(filter=FieldFilter("entity_links", "array_contains", node_uuid))
        .where(filter=FieldFilter("user_id", "==", user_id))
        .limit(TRAVERSE_NEIGHBOR_QUERY_LIMIT)
    )
    ids: list[str] = []
    try:
        async for doc in q.stream():
            ids.append(doc.id)
    except Exception as e:
        log.warning("_get_nodes_linking_to failed: %s", e)
    return ids


async def graph_expand(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    seed_ids: list[str],
    query: Optional[str],
    hops: int,
    limit_per_edge: int,
    user_id: str,
    self_seed_neighbor_floor: int = 40,
) -> GraphExpandResponse:
    log.info(
        "graph_expand:start seeds=%d hops=%d limit_per_edge=%d user_id=%s has_query=%s",
        len(seed_ids),
        hops,
        limit_per_edge,
        user_id,
        bool(query),
    )
    query_vector: Optional[list[float]] = None
    if query:
        query_vector = await embedder.embed(query, EMBEDDING_TASK_RETRIEVAL_QUERY)

    visited: set[str] = set()
    discovered: set[str] = set()
    all_nodes: dict[str, Node] = {}
    all_edges: list[Edge] = []

    # Seed fetch — exclude log entries from graph
    seed_nodes = await _fetch_nodes_by_ids(db, config, seed_ids)
    visited.update(seed_ids)
    discovered.update(seed_ids)
    for node in seed_nodes.values():
        if node.node_type != NODE_TYPE_LOG and _is_alive(node):
            all_nodes[node.uuid] = node

    frontier = [
        n for n in seed_nodes.values() if n.node_type != NODE_TYPE_LOG and _is_alive(n)
    ]
    log.info(
        "graph_expand:seed_fetch requested=%d found=%d frontier=%d",
        len(seed_ids),
        len(seed_nodes),
        len(frontier),
    )

    sem = asyncio.Semaphore(10)

    for hop_idx in range(hops):
        if not frontier:
            log.info("graph_expand:hop=%d frontier_empty", hop_idx + 1)
            break

        next_ids: set[str] = set()
        self_neighbor_ids: set[str] = set()
        self_seed_id = stable_self_id(user_id)
        self_in_frontier = any(node.uuid == self_seed_id for node in frontier)

        gather_tasks = [
            _gather_neighbors(db, config, node, user_id, sem)
            for node in frontier
        ]
        neighbor_lists = await asyncio.gather(*gather_tasks)

        for node, (incoming_spo, linking_to) in zip(frontier, neighbor_lists):
            candidate_ids = set(incoming_spo) | set(linking_to)

            if node.object_uuid:
                candidate_ids.add(node.object_uuid)
            candidate_ids.update(node.entity_links)
            candidate_ids -= discovered
            next_ids.update(candidate_ids)
            if hop_idx == 0 and node.uuid == self_seed_id:
                self_neighbor_ids.update(candidate_ids)

            if (
                node.node_type == "relationship"
                and node.subject_uuid
                and node.object_uuid
                and _is_alive(node)
            ):
                all_edges.append(Edge(
                    subject=node.subject_uuid,
                    predicate=node.predicate or "related_to",
                    object=node.object_uuid,
                ))

        if not next_ids:
            log.info("graph_expand:hop=%d no_next_ids frontier=%d", hop_idx + 1, len(frontier))
            break
        discovered.update(next_ids)

        candidates = await _fetch_nodes_by_ids(db, config, list(next_ids))
        # Log entries: include in all_nodes as context but never put in the
        # frontier — they don't lead to useful graph neighbours.
        # Tombstones (weight 0): mark visited so we do not keep re-fetching them.
        for n in candidates.values():
            if not _is_alive(n):
                visited.add(n.uuid)
                continue
            if n.node_type == NODE_TYPE_LOG:
                visited.add(n.uuid)
                all_nodes[n.uuid] = n  # keep as journal context in response

        non_log = [
            n for n in candidates.values() if n.node_type != NODE_TYPE_LOG and _is_alive(n)
        ]
        self_neighbors = [n for n in non_log if n.uuid in self_neighbor_ids]

        pruned = prune_frontier_by_similarity(non_log, query_vector, limit_per_edge)
        pruned = apply_self_seed_neighbor_floor(
            pruned=pruned,
            self_neighbors=self_neighbors,
            query_vector=query_vector,
            floor=self_seed_neighbor_floor,
            hop_idx=hop_idx,
            self_in_frontier=self_in_frontier,
        )

        for node in pruned:
            if node.uuid not in visited:
                visited.add(node.uuid)
                all_nodes[node.uuid] = node

        frontier = pruned
        log.info(
            "graph_expand:hop=%d frontier_in=%d next_ids=%d candidates=%d non_log=%d pruned=%d logs=%d total_nodes=%d total_edges=%d",
            hop_idx + 1,
            len(neighbor_lists),
            len(next_ids),
            len(candidates),
            len(non_log),
            len(pruned),
            len(candidates) - len(non_log),
            len(all_nodes),
            len(all_edges),
        )

    log.info("graph_expand:done nodes=%d edges=%d", len(all_nodes), len(all_edges))
    return GraphExpandResponse(nodes=all_nodes, edges=all_edges)


async def _gather_neighbors(
    db: firestore.AsyncClient,
    config: Config,
    node: Node,
    user_id: str,
    sem: asyncio.Semaphore,
) -> tuple[list[str], list[str]]:
    async with sem:
        incoming, linking = await asyncio.gather(
            _get_incoming_spo_edges(db, config, node.uuid, user_id),
            _get_nodes_linking_to(db, config, node.uuid, user_id),
        )
    return incoming, linking
