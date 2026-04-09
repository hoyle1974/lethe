from __future__ import annotations
import asyncio
from typing import Optional

from google.cloud import firestore

from lethe.config import Config
from lethe.graph.search import cosine_similarity, doc_to_node
from lethe.infra.embedder import Embedder
from lethe.models.node import Node, Edge, GraphExpandResponse

_BATCH_SIZE = 100


def prune_frontier_by_similarity(
    nodes: list[Node],
    query_vector: Optional[list[float]],
    top_k: int,
) -> list[Node]:
    if len(nodes) <= top_k:
        return nodes
    if query_vector is None:
        return nodes[:top_k]
    scored = [
        (n, cosine_similarity(n.embedding, query_vector) if n.embedding else 0.0)
        for n in nodes
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [n for n, _ in scored[:top_k]]


async def _fetch_nodes_by_ids(
    db: firestore.AsyncClient,
    config: Config,
    ids: list[str],
) -> dict[str, Node]:
    col = db.collection(config.lethe_collection)
    result: dict[str, Node] = {}
    for i in range(0, len(ids), _BATCH_SIZE):
        chunk = ids[i:i + _BATCH_SIZE]
        refs = [col.document(uid) for uid in chunk]
        snaps = await db.get_all(refs)
        for snap in snaps:
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
        .limit(50)
    )
    ids: list[str] = []
    async for doc in q.stream():
        ids.append(doc.id)
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
        .limit(50)
    )
    ids: list[str] = []
    async for doc in q.stream():
        ids.append(doc.id)
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
) -> GraphExpandResponse:
    query_vector: Optional[list[float]] = None
    if query:
        query_vector = await embedder.embed(query, "RETRIEVAL_QUERY")

    visited: set[str] = set()
    all_nodes: dict[str, Node] = {}
    all_edges: list[Edge] = []

    # Seed fetch
    seed_nodes = await _fetch_nodes_by_ids(db, config, seed_ids)
    all_nodes.update(seed_nodes)
    visited.update(seed_ids)

    frontier = list(seed_nodes.values())

    for _hop in range(hops):
        if not frontier:
            break

        next_ids: set[str] = set()

        gather_tasks = [
            _gather_neighbors(db, config, node, user_id)
            for node in frontier
        ]
        neighbor_lists = await asyncio.gather(*gather_tasks)

        for node, (incoming_spo, linking_to) in zip(frontier, neighbor_lists):
            candidate_ids = set(incoming_spo) | set(linking_to)

            if node.object_uuid:
                candidate_ids.add(node.object_uuid)
            candidate_ids.update(node.entity_links)
            candidate_ids -= visited
            next_ids.update(candidate_ids)

            if node.node_type == "relationship" and node.subject_uuid and node.object_uuid:
                all_edges.append(Edge(
                    subject=node.subject_uuid,
                    predicate=node.predicate or "related_to",
                    object=node.object_uuid,
                ))

        if not next_ids:
            break

        candidates = await _fetch_nodes_by_ids(db, config, list(next_ids))
        candidate_list = list(candidates.values())
        pruned = prune_frontier_by_similarity(candidate_list, query_vector, limit_per_edge)

        for node in pruned:
            if node.uuid not in visited:
                visited.add(node.uuid)
                all_nodes[node.uuid] = node

        frontier = pruned

    return GraphExpandResponse(nodes=all_nodes, edges=all_edges)


async def _gather_neighbors(
    db: firestore.AsyncClient,
    config: Config,
    node: Node,
    user_id: str,
) -> tuple[list[str], list[str]]:
    incoming, linking = await asyncio.gather(
        _get_incoming_spo_edges(db, config, node.uuid, user_id),
        _get_nodes_linking_to(db, config, node.uuid, user_id),
    )
    return incoming, linking
