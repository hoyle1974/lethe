from __future__ import annotations
import hashlib
import re
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from google.cloud import firestore

from lethe.config import Config
from lethe.infra.embedder import Embedder
from lethe.models.node import Node

if TYPE_CHECKING:
    from lethe.infra.llm import LLMDispatcher


def stable_entity_doc_id(node_type: str, name: str) -> str:
    key = node_type + ":" + name.lower().strip()
    digest = hashlib.sha1(key.encode()).hexdigest()
    return "entity_" + digest


def stable_rel_id(subject_id: str, predicate: str, object_id: str) -> str:
    key = subject_id + ":" + predicate + ":" + object_id
    digest = hashlib.sha1(key.encode()).hexdigest()
    return "rel_" + digest


def normalized_predicate(raw: str) -> str:
    """Lowercase, strip, replace spaces/hyphens with underscores. Strip NEW: prefix."""
    p = raw.strip()
    if p.upper().startswith("NEW:"):
        p = p[4:].strip()
    return re.sub(r"[\s\-]+", "_", p).lower()


def _new_uuid() -> str:
    return str(_uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc_to_node(doc_id: str, data: dict) -> Node:
    embedding = None
    raw_emb = data.get("embedding")
    if raw_emb is not None:
        try:
            embedding = list(raw_emb)
        except TypeError:
            embedding = None
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


async def _find_nearest_by_type(
    collection,
    vector: list[float],
    node_type: str,
    threshold: float,
) -> Optional[Node]:
    """Vector ANN search restricted to node_type, returning nearest match within threshold."""
    try:
        query = collection.find_nearest(
            vector_field="embedding",
            query_vector=firestore.Vector(vector),
            distance_measure=firestore.DistanceMeasure.COSINE,
            limit=5,
            distance_result_field="__vector_distance__",
        )
        async for doc in query.stream():
            data = doc.to_dict() or {}
            dist = data.pop("__vector_distance__", 1.0)
            if data.get("node_type") != node_type:
                continue
            if float(dist) <= threshold:
                return _doc_to_node(doc.id, data)
    except Exception:
        pass
    return None


async def ensure_node(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    identifier: str,
    node_type: str,
    source_entry_id: str,
    timestamp: str,
    user_id: str = "global",
    llm: Optional[LLMDispatcher] = None,
) -> Node:
    """Return an existing entity node or create one.

    Resolution order:
    1. Vector search by node_type within entity_threshold.
    2. SHA1 stable doc ID fast path.
    3. name_key exact match.
    4. Create new node.
    """
    clean = identifier.strip()
    if not clean:
        raise ValueError("ensure_node: empty identifier")

    collection = db.collection(config.lethe_collection)
    vector = await embedder.embed(clean, "RETRIEVAL_DOCUMENT")

    # Step 1: semantic search
    nearest = await _find_nearest_by_type(
        collection, vector, node_type, config.lethe_entity_threshold
    )
    if nearest is not None:
        if llm is not None and config.lethe_collision_detection:
            from lethe.graph.collision import evaluate_fact_collision
            action = await evaluate_fact_collision(llm, clean, nearest.content)
            if action == "update":
                new_vector = await embedder.embed(clean, "RETRIEVAL_DOCUMENT")
                await collection.document(nearest.uuid).update({
                    "content": clean,
                    "embedding": firestore.Vector(new_vector),
                    "updated_at": _now_iso(),
                })
        if source_entry_id:
            await collection.document(nearest.uuid).update({
                "journal_entry_ids": firestore.ArrayUnion([source_entry_id]),
                "updated_at": _now_iso(),
            })
        return nearest

    # Step 2: SHA1 stable doc ID
    doc_id = stable_entity_doc_id(node_type, clean)
    ref = collection.document(doc_id)
    ts = timestamp or _now_iso()

    # Step 3: name_key exact match (outside transaction — Firestore async transactions
    # don't support queries; check before committing)
    name_key = clean.lower()
    try:
        existing_query = (
            collection
            .where(filter=firestore.FieldFilter("name_key", "==", name_key))
            .where(filter=firestore.FieldFilter("node_type", "==", node_type))
            .limit(1)
        )
        async for existing_doc in existing_query.stream():
            existing_data = existing_doc.to_dict() or {}
            if source_entry_id:
                await collection.document(existing_doc.id).update({
                    "journal_entry_ids": firestore.ArrayUnion([source_entry_id]),
                    "updated_at": ts,
                })
            return _doc_to_node(existing_doc.id, existing_data)
    except Exception:
        pass

    # Step 4: create new node
    snap = await ref.get()
    if snap.exists:
        data = snap.to_dict() or {}
        if source_entry_id:
            await ref.update({
                "journal_entry_ids": firestore.ArrayUnion([source_entry_id]),
                "updated_at": ts,
            })
        return _doc_to_node(doc_id, data)

    node_data = {
        "node_type": node_type,
        "content": clean,
        "name_key": name_key,
        "domain": "entity",
        "weight": 0.55,
        "metadata": "{}",
        "entity_links": [],
        "journal_entry_ids": [source_entry_id] if source_entry_id else [],
        "embedding": firestore.Vector(vector),
        "user_id": user_id,
        "created_at": ts,
        "updated_at": ts,
    }
    await ref.set(node_data)
    return _doc_to_node(doc_id, node_data)


async def add_entity_link(
    db: firestore.AsyncClient,
    config: Config,
    node_uuid: str,
    link_uuid: str,
) -> None:
    ref = db.collection(config.lethe_collection).document(node_uuid)
    await ref.update({"entity_links": firestore.ArrayUnion([link_uuid])})


async def update_hot_edges(
    db: firestore.AsyncClient,
    config: Config,
    object_node_id: str,
    new_rel_id: str,
) -> None:
    """Maintain a bounded hot_edges array; evict lowest relevance_score when full."""
    col = db.collection(config.lethe_collection)
    try:
        await col.document(new_rel_id).update({"relevance_score": 1.0})
    except Exception:
        pass

    try:
        obj_snap = await col.document(object_node_id).get()
        if not obj_snap.exists:
            return
        data = obj_snap.to_dict() or {}
        hot_edges: list[str] = list(data.get("hot_edges", []))

        if len(hot_edges) < config.lethe_max_hot_edges:
            hot_edges.append(new_rel_id)
            await col.document(object_node_id).update({"hot_edges": hot_edges})
            return

        # Evict lowest relevance_score
        scores: list[tuple[str, float]] = []
        for eid in hot_edges:
            snap = await col.document(eid).get()
            score = (snap.to_dict() or {}).get("relevance_score", 0.0) if snap.exists else 0.0
            scores.append((eid, float(score)))

        lowest_idx = min(range(len(scores)), key=lambda i: scores[i][1])
        hot_edges[lowest_idx] = new_rel_id
        await col.document(object_node_id).update({"hot_edges": hot_edges})
    except Exception:
        pass


async def create_relationship_node(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    subject_id: str,
    predicate: str,
    object_id: str,
    source_entry_id: str,
    subject_content: str,
    object_content: str,
    timestamp: str,
    user_id: str = "global",
) -> str:
    """Create or update a reified relationship node. Returns the relationship document ID."""
    predicate = normalized_predicate(predicate)
    if not subject_id or not object_id or not predicate:
        raise ValueError("create_relationship_node: subject, predicate, object required")

    rel_id = stable_rel_id(subject_id, predicate, object_id)
    col = db.collection(config.lethe_collection)
    ref = col.document(rel_id)
    ts = timestamp or _now_iso()

    snap = await ref.get()
    if snap.exists:
        updates: dict = {"updated_at": ts}
        if source_entry_id:
            updates["journal_entry_ids"] = firestore.ArrayUnion([source_entry_id])
        await ref.update(updates)
        return rel_id

    content = f"{subject_content} {predicate} {object_content}".strip()
    vector = await embedder.embed(content, "RETRIEVAL_DOCUMENT")

    data = {
        "node_type": "relationship",
        "content": content,
        "predicate": predicate,
        "subject_uuid": subject_id,
        "object_uuid": object_id,
        "entity_links": [subject_id, object_id],
        "journal_entry_ids": [source_entry_id] if source_entry_id else [],
        "domain": "relationship",
        "weight": 0.8,
        "metadata": "{}",
        "embedding": firestore.Vector(vector),
        "relevance_score": 1.0,
        "user_id": user_id,
        "created_at": ts,
        "updated_at": ts,
    }
    await ref.set(data)
    return rel_id
