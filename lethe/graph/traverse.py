from __future__ import annotations

import asyncio
import logging
from typing import Optional

from google.cloud import firestore

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
from lethe.graph.search import cosine_similarity
from lethe.graph.serialization import doc_to_edge, doc_to_node
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import FieldFilter
from lethe.models.node import Edge, GraphExpandResponse, Node

log = logging.getLogger(__name__)


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


def _passes_source_filter(node: Node, source_filter: Optional[str]) -> bool:
    if source_filter is None:
        return True
    return node.source is None or node.source == source_filter


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
                )
                * TRAVERSAL_SIMILARITY_WEIGHT
            )
            + (
                (
                    (len(n.journal_entry_ids) / max_observation_count)
                    if max_observation_count
                    else 0.0
                )
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
        chunk = ids[i : i + TRAVERSE_BATCH_SIZE]
        refs = [col.document(uid) for uid in chunk]
        async for snap in db.get_all(refs):
            if snap.exists:
                data = snap.to_dict() or {}
                result[snap.id] = doc_to_node(snap.id, data)
    return result


async def _get_edge_neighbors(
    db: firestore.AsyncClient,
    config: Config,
    node_uuid: str,
    user_id: str,
) -> list[Edge]:
    """Return all edges from the relationships collection where node_uuid is subject or object."""
    col = db.collection(config.lethe_relationships_collection)

    async def _query_field(field: str) -> list[Edge]:
        q = (
            col.where(filter=FieldFilter(field, "==", node_uuid))
            .where(filter=FieldFilter("user_id", "==", user_id))
            .limit(TRAVERSE_NEIGHBOR_QUERY_LIMIT)
        )
        edges: list[Edge] = []
        try:
            async for doc in q.stream():
                data = doc.to_dict() or {}
                edges.append(doc_to_edge(doc.id, data))
        except Exception as e:
            log.warning("_get_edge_neighbors(%s) failed: %s", field, e)
        return edges

    outgoing, incoming = await asyncio.gather(
        _query_field("subject_uuid"),
        _query_field("object_uuid"),
    )
    seen: set[str] = set()
    result: list[Edge] = []
    for edge in outgoing + incoming:
        if edge.uuid not in seen:
            seen.add(edge.uuid)
            result.append(edge)
    return result


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
    seen_edge_uuids: set[str] = set()

    seed_nodes = await _fetch_nodes_by_ids(db, config, seed_ids)
    visited.update(seed_ids)
    discovered.update(seed_ids)
    for node in seed_nodes.values():
        if node.node_type != NODE_TYPE_LOG and _is_alive(node):
            all_nodes[node.uuid] = node

    frontier = [n for n in seed_nodes.values() if n.node_type != NODE_TYPE_LOG and _is_alive(n)]
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

        gather_tasks = [_gather_neighbors(db, config, node, user_id, sem) for node in frontier]
        edge_lists = await asyncio.gather(*gather_tasks)

        for node, edges in zip(frontier, edge_lists):
            for edge in edges:
                if edge.weight > 0.0 and edge.uuid not in seen_edge_uuids:
                    seen_edge_uuids.add(edge.uuid)
                    all_edges.append(edge)
                other = edge.object_uuid if edge.subject_uuid == node.uuid else edge.subject_uuid
                if other not in discovered:
                    next_ids.add(other)
                    if hop_idx == 0 and node.uuid == self_seed_id:
                        self_neighbor_ids.add(other)

        if not next_ids:
            log.info("graph_expand:hop=%d no_next_ids frontier=%d", hop_idx + 1, len(frontier))
            break
        discovered.update(next_ids)

        candidates = await _fetch_nodes_by_ids(db, config, list(next_ids))
        for n in candidates.values():
            if not _is_alive(n):
                visited.add(n.uuid)
                continue
            if n.node_type == NODE_TYPE_LOG:
                visited.add(n.uuid)
                all_nodes[n.uuid] = n

        non_log = [n for n in candidates.values() if n.node_type != NODE_TYPE_LOG and _is_alive(n)]
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
            "graph_expand:hop=%d frontier_in=%d next_ids=%d candidates=%d "
            "non_log=%d pruned=%d logs=%d total_nodes=%d total_edges=%d",
            hop_idx + 1,
            len(edge_lists),
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
) -> list[Edge]:
    async with sem:
        return await _get_edge_neighbors(db, config, node.uuid, user_id)
