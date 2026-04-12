from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

from lethe.config import Config
from lethe.constants import (
    DEFAULT_DOMAIN,
    DEFAULT_ENTITY_WEIGHT,
    DEFAULT_NODE_TYPE,
    DEFAULT_RELATIONSHIP_WEIGHT,
    DEFAULT_USER_ID,
    EMBEDDING_TASK_RETRIEVAL_DOCUMENT,
    NODE_TYPE_ENTITY,
    NODE_TYPE_RELATIONSHIP,
    RELATIONSHIP_SUPERSEDE_CANDIDATE_LIMIT,
)
from lethe.graph.contradiction import evaluate_relationship_supersedes, tombstone_relationship
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import ArrayUnion, DistanceMeasure, FieldFilter, Vector
from lethe.infra.llm import LLMDispatcher
from lethe.models.node import Node

log = logging.getLogger(__name__)


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


_ENTITY_DOC_ID_RE = re.compile(r"^entity_[0-9a-f]{40}$", re.IGNORECASE)


def stable_entity_doc_id(node_type: str, name: str) -> str:
    key = node_type + ":" + name.lower().strip()
    digest = hashlib.sha1(key.encode()).hexdigest()
    return "entity_" + digest


def stable_self_id(user_id: str) -> str:
    """Return a deterministic ID for the account owner."""
    key = f"self:{user_id}"
    digest = hashlib.sha1(key.encode()).hexdigest()
    return f"entity_{digest}"


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        entity_links=list(data.get("entity_links", [])),
        predicate=data.get("predicate"),
        object_uuid=data.get("object_uuid"),
        subject_uuid=data.get("subject_uuid"),
        journal_entry_ids=list(data.get("journal_entry_ids", [])),
        name_key=data.get("name_key"),
        relevance_score=data.get("relevance_score"),
        user_id=data.get("user_id", DEFAULT_USER_ID),
        source=data.get("source"),
        created_at=parse_to_utc(data.get("created_at")),
        updated_at=parse_to_utc(data.get("updated_at")),
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
        query = collection.where(filter=FieldFilter("node_type", "==", node_type)).find_nearest(
            vector_field="embedding",
            query_vector=Vector(vector),
            distance_measure=DistanceMeasure.COSINE,
            limit=5,
            distance_result_field="vector_distance",
        )
        async for doc in query.stream():
            data = doc.to_dict() or {}
            dist = data.pop("vector_distance", 1.0)
            if float(dist) <= threshold:
                return doc_to_node(doc.id, data)
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
    user_id: str = DEFAULT_USER_ID,
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

    if clean.upper() == "SELF":
        clean = "Me"
        doc_id = stable_self_id(user_id)
        ref = collection.document(doc_id)
        ts = timestamp or _now_iso()
        snap = await ref.get()
        if snap.exists:
            data = snap.to_dict() or {}
            if source_entry_id:
                await ref.update(
                    {
                        "journal_entry_ids": ArrayUnion([source_entry_id]),
                        "updated_at": ts,
                    }
                )
                data["journal_entry_ids"] = list(
                    set(list(data.get("journal_entry_ids", [])) + [source_entry_id])
                )
                data["updated_at"] = ts
            return doc_to_node(doc_id, data)

        vector = await embedder.embed(clean, EMBEDDING_TASK_RETRIEVAL_DOCUMENT)
        node_data = {
            "node_type": "person",
            "content": clean,
            "name_key": clean.lower(),
            "domain": NODE_TYPE_ENTITY,
            "weight": DEFAULT_ENTITY_WEIGHT,
            "metadata": "{}",
            "entity_links": [],
            "journal_entry_ids": [source_entry_id] if source_entry_id else [],
            "embedding": Vector(vector),
            "user_id": user_id,
            "created_at": ts,
            "updated_at": ts,
        }
        await ref.set(node_data, merge=False)
        return doc_to_node(doc_id, node_data)

    # Strict internal-ID path: only reuse existing entity docs; never create from ID-like text.
    if _looks_like_entity_doc_id(clean):
        id_ref = collection.document(clean)
        id_snap = await id_ref.get()
        if not id_snap.exists:
            raise ValueError(f"ensure_node: entity id not found: {clean}")
        existing = id_snap.to_dict() or {}
        if source_entry_id:
            await id_ref.update(
                {
                    "journal_entry_ids": ArrayUnion([source_entry_id]),
                    "updated_at": timestamp or _now_iso(),
                }
            )
        return doc_to_node(clean, existing)

    vector = await embedder.embed(clean, EMBEDDING_TASK_RETRIEVAL_DOCUMENT)

    # Step 1: semantic search
    nearest = await _find_nearest_by_type(
        collection, vector, node_type, config.lethe_entity_threshold
    )
    if nearest is not None:
        if llm is not None and config.lethe_collision_detection:
            from lethe.graph.collision import evaluate_fact_collision

            action = await evaluate_fact_collision(llm, clean, nearest.content)
            if action == "update":
                new_vector = await embedder.embed(clean, EMBEDDING_TASK_RETRIEVAL_DOCUMENT)
                await collection.document(nearest.uuid).update(
                    {
                        "content": clean,
                        "name_key": clean.lower(),
                        "embedding": Vector(new_vector),
                        "updated_at": _now_iso(),
                    }
                )
        if source_entry_id:
            await collection.document(nearest.uuid).update(
                {
                    "journal_entry_ids": ArrayUnion([source_entry_id]),
                    "updated_at": _now_iso(),
                }
            )
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
            collection.where(filter=FieldFilter("name_key", "==", name_key))
            .where(filter=FieldFilter("node_type", "==", node_type))
            .limit(1)
        )
        async for existing_doc in existing_query.stream():
            existing_data = existing_doc.to_dict() or {}
            if source_entry_id:
                await collection.document(existing_doc.id).update(
                    {
                        "journal_entry_ids": ArrayUnion([source_entry_id]),
                        "updated_at": ts,
                    }
                )
            return doc_to_node(existing_doc.id, existing_data)
    except Exception:
        pass

    # Step 4: create new node in a Firestore transaction to prevent lost writes
    # under concurrent ingestion of the same entity.
    node_data = {
        "node_type": node_type,
        "content": clean,
        "name_key": name_key,
        "domain": NODE_TYPE_ENTITY,
        "weight": DEFAULT_ENTITY_WEIGHT,
        "metadata": "{}",
        "entity_links": [],
        "journal_entry_ids": [source_entry_id] if source_entry_id else [],
        "embedding": Vector(vector),
        "user_id": user_id,
        "created_at": ts,
        "updated_at": ts,
    }

    @firestore.async_transactional
    async def _txn_create_or_get(transaction: firestore.AsyncTransaction) -> Node:
        snap = await ref.get(transaction=transaction)
        if snap.exists:
            data = snap.to_dict() or {}
            if source_entry_id:
                transaction.update(
                    ref,
                    {
                        "journal_entry_ids": ArrayUnion([source_entry_id]),
                        "updated_at": ts,
                    },
                )
            return doc_to_node(doc_id, data)
        transaction.set(ref, node_data)
        return doc_to_node(doc_id, node_data)

    return await _txn_create_or_get(db.transaction())


def _looks_like_entity_doc_id(identifier: str) -> bool:
    return bool(_ENTITY_DOC_ID_RE.fullmatch(identifier.strip()))


async def add_entity_link(
    db: firestore.AsyncClient,
    config: Config,
    node_uuid: str,
    link_uuid: str,
) -> None:
    ref = db.collection(config.lethe_collection).document(node_uuid)
    await ref.update({"entity_links": ArrayUnion([link_uuid])})


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
    user_id: str = DEFAULT_USER_ID,
    llm: Optional[LLMDispatcher] = None,
) -> str:
    """Create or update a reified relationship node. Returns the relationship document ID."""
    predicate = normalized_predicate(predicate)
    if not subject_id or not object_id or not predicate:
        raise ValueError("create_relationship_node: subject, predicate, object required")

    rel_id = stable_rel_id(subject_id, predicate, object_id)
    col = db.collection(config.lethe_collection)
    ref = col.document(rel_id)
    ts = timestamp or _now_iso()

    # Pre-compute embedding before the transaction — async I/O is not allowed
    # inside a Firestore transactional function.
    content = f"{subject_content} {predicate} {object_content}".strip()
    vector = await embedder.embed(content, EMBEDDING_TASK_RETRIEVAL_DOCUMENT)

    superseded_id: Optional[str] = None
    existing_facts: list[tuple[str, str]] = []
    try:
        rq = (
            col.where(filter=FieldFilter("user_id", "==", user_id))
            .where(filter=FieldFilter("subject_uuid", "==", subject_id))
            .where(filter=FieldFilter("node_type", "==", NODE_TYPE_RELATIONSHIP))
            .order_by("updated_at", direction=firestore.Query.DESCENDING)
            .limit(RELATIONSHIP_SUPERSEDE_CANDIDATE_LIMIT)
        )
        async for doc in rq.stream():
            if doc.id == rel_id:
                continue
            d = doc.to_dict() or {}
            c = (d.get("content") or "").strip()
            if c:
                existing_facts.append((doc.id, c))
    except Exception as e:
        log.warning("create_relationship_node: existing rel query failed: %s", e)

    if llm is not None and existing_facts:
        superseded_id = await evaluate_relationship_supersedes(llm, content, existing_facts)

    create_data = {
        "node_type": NODE_TYPE_RELATIONSHIP,
        "content": content,
        "predicate": predicate,
        "subject_uuid": subject_id,
        "object_uuid": object_id,
        "entity_links": [subject_id, object_id],
        "journal_entry_ids": [source_entry_id] if source_entry_id else [],
        "domain": NODE_TYPE_RELATIONSHIP,
        "weight": DEFAULT_RELATIONSHIP_WEIGHT,
        "metadata": "{}",
        "embedding": Vector(vector),
        "relevance_score": 1.0,
        "user_id": user_id,
        "created_at": ts,
        "updated_at": ts,
    }

    @firestore.async_transactional
    async def _txn_create_or_update(transaction: firestore.AsyncTransaction) -> str:
        snap = await ref.get(transaction=transaction)
        if snap.exists:
            updates: dict = {"updated_at": ts}
            if source_entry_id:
                updates["journal_entry_ids"] = ArrayUnion([source_entry_id])
            transaction.update(ref, updates)
            return rel_id
        transaction.set(ref, create_data)
        return rel_id

    rid = await _txn_create_or_update(db.transaction())
    if superseded_id and superseded_id != rid:
        candidate_ids = {u for u, _ in existing_facts}
        if superseded_id in candidate_ids:
            await tombstone_relationship(db, config.lethe_collection, superseded_id, rid)
    return rid
