from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

from lethe.config import Config
from lethe.constants import (
    DEFAULT_DOMAIN,
    DEFAULT_ENTITY_WEIGHT,
    DEFAULT_LOG_WEIGHT,
    DEFAULT_NODE_TYPE,
    DEFAULT_USER_ID,
    EMBEDDING_TASK_RETRIEVAL_DOCUMENT,
    NODE_TYPE_LOG,
)
from lethe.graph.canonical_map import CanonicalMap, append_predicate
from lethe.graph.ensure_node import (
    create_relationship_node,
    ensure_node,
    stable_entity_doc_id,
    stable_self_id,
)
from lethe.graph.extraction import RefineryTriple, extract_triples
from lethe.graph.ids import is_generated_id
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import ArrayUnion, Vector
from lethe.infra.llm import LLMDispatcher
from lethe.models.node import IngestResponse, Node

log = logging.getLogger(__name__)

_PLACEHOLDER_TERMS = {
    DEFAULT_NODE_TYPE,
    "unknown",
    "none",
    "null",
    "n/a",
    "na",
    "unspecified",
}


async def run_ingest(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    canonical_map: CanonicalMap,
    text: str,
    domain: str = DEFAULT_DOMAIN,
    source: Optional[str] = None,
    user_id: str = DEFAULT_USER_ID,
    timestamp: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> IngestResponse:
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    entry_uuid = str(uuid.uuid4())

    # Step 1: store episodic log entry
    vector = await embedder.embed(text, EMBEDDING_TASK_RETRIEVAL_DOCUMENT)
    col = db.collection(config.lethe_collection)
    await col.document(entry_uuid).set(
        {
            "node_type": NODE_TYPE_LOG,
            "content": text,
            "domain": domain,
            "weight": DEFAULT_LOG_WEIGHT,
            "metadata": json.dumps(metadata) if metadata else "{}",
            "embedding": Vector(vector),
            "user_id": user_id,
            "source": source,
            "created_at": ts,
            "updated_at": ts,
        }
    )

    log.info("ingest: entry_uuid=%s text=%r", entry_uuid, text[:120])

    # Step 2: extract SPO triples
    status, triples = await extract_triples(
        llm=llm,
        text=text,
        node_types=canonical_map.node_types,
        allowed_predicates=canonical_map.allowed_predicates,
    )
    log.info("ingest: extraction status=%s triples=%d", status, len(triples))
    for t in triples:
        log.debug(
            "ingest: triple subject=%r predicate=%r object=%r",
            t.subject,
            t.canonical_predicate,
            t.object,
        )
    if status == "none" or not triples:
        log.warning("ingest: no triples extracted — entry_uuid=%s", entry_uuid)
        return IngestResponse(entry_uuid=entry_uuid)

    nodes_created: list[str] = []
    nodes_updated: list[str] = []
    relationships_created: list[str] = []
    rejection_counts = {
        "unresolved_internal_id_or_placeholder": 0,
        "triple_processing_error": 0,
    }

    # Step 3: resolve and commit each triple
    for triple in triples:
        try:
            outcome = await _process_triple(
                db=db,
                embedder=embedder,
                llm=llm,
                config=config,
                triple=triple,
                entry_uuid=entry_uuid,
                ts=ts,
                user_id=user_id,
                nodes_created=nodes_created,
                nodes_updated=nodes_updated,
                relationships_created=relationships_created,
                canonical_map=canonical_map,
            )
            if outcome and outcome in rejection_counts:
                rejection_counts[outcome] += 1
        except Exception as e:
            log.error("ingest: _process_triple failed for %r: %s", triple, e, exc_info=True)
            rejection_counts["triple_processing_error"] += 1
            continue

    log.info(
        "ingest: complete entry_uuid=%s nodes_created=%d nodes_updated=%d relationships=%d",
        entry_uuid,
        len(nodes_created),
        len(nodes_updated),
        len(relationships_created),
    )
    if any(v > 0 for v in rejection_counts.values()):
        log.info(
            "ingest: triple rejection summary entry_uuid=%s counts=%s", entry_uuid, rejection_counts
        )
    return IngestResponse(
        entry_uuid=entry_uuid,
        nodes_created=nodes_created,
        nodes_updated=nodes_updated,
        relationships_created=relationships_created,
    )


async def _process_triple(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    triple: RefineryTriple,
    entry_uuid: str,
    ts: str,
    user_id: str,
    nodes_created: list[str],
    nodes_updated: list[str],
    relationships_created: list[str],
    canonical_map: CanonicalMap,
) -> str:
    predicate = triple.canonical_predicate
    if triple.is_new_predicate:
        await append_predicate(db, predicate)
        if predicate not in canonical_map.allowed_predicates:
            canonical_map.allowed_predicates.append(predicate)

    subj_resolved = await _resolve_term(db, config, triple.subject, triple.subject_type, user_id)
    obj_resolved = await _resolve_term(db, config, triple.object, triple.object_type, user_id)

    if subj_resolved is None or obj_resolved is None:
        # Skip clearly invalid internal-ID triples but keep the source log entry.
        log.warning(
            "ingest: dropping triple with unresolved internal id subject=%r object=%r",
            triple.subject,
            triple.object,
        )
        return "unresolved_internal_id_or_placeholder"

    subj_exists, subj_node = await _get_or_create_entity_node(
        db=db,
        embedder=embedder,
        llm=llm,
        config=config,
        resolved_term=subj_resolved,
        fallback_type=triple.subject_type,
        entry_uuid=entry_uuid,
        ts=ts,
        user_id=user_id,
    )
    obj_exists, obj_node = await _get_or_create_entity_node(
        db=db,
        embedder=embedder,
        llm=llm,
        config=config,
        resolved_term=obj_resolved,
        fallback_type=triple.object_type,
        entry_uuid=entry_uuid,
        ts=ts,
        user_id=user_id,
    )

    _track(subj_node.uuid, subj_exists, nodes_created, nodes_updated)
    _track(obj_node.uuid, obj_exists, nodes_created, nodes_updated)

    rel_id = await create_relationship_node(
        db=db,
        embedder=embedder,
        config=config,
        subject_id=subj_node.uuid,
        predicate=predicate,
        object_id=obj_node.uuid,
        source_entry_id=entry_uuid,
        subject_content=subj_node.content,
        object_content=obj_node.content,
        timestamp=ts,
        user_id=user_id,
        llm=llm,
    )
    if rel_id not in relationships_created:
        relationships_created.append(rel_id)

    return "ok"


def _looks_like_generated_id(value: str) -> bool:
    return is_generated_id(value.strip())


async def _node_exists(
    db: firestore.AsyncClient, config: Config, node_type: str, name: str
) -> bool:
    doc_id = stable_entity_doc_id(node_type, name)
    snap = await db.collection(config.lethe_collection).document(doc_id).get()
    return snap.exists


async def _resolve_term(
    db: firestore.AsyncClient,
    config: Config,
    raw_term: str,
    node_type: Optional[str] = None,
    user_id: str = DEFAULT_USER_ID,
) -> Optional[dict]:
    """Resolve internal IDs to existing node content before ensure_node."""
    term = (raw_term or "").strip()
    if not term:
        return None
    if term.upper() == "SELF":
        return {
            "text": "Me",
            "existing_uuid": stable_self_id(user_id),
            "resolved_type": "person",
            "self_token": True,
        }
    if _looks_like_placeholder_term(term, node_type):
        return None
    if not _looks_like_generated_id(term):
        return {"text": term, "existing_uuid": None, "resolved_type": None}

    ref = db.collection(config.lethe_collection).document(term)
    snap = await ref.get()
    if not snap.exists:
        return None

    data = snap.to_dict() or {}
    content = (data.get("content") or "").strip()
    if not content or _looks_like_generated_id(content):
        return None

    return {
        "text": content,
        "existing_uuid": term,
        "resolved_type": data.get("node_type"),
    }


def _looks_like_placeholder_term(value: str, node_type: Optional[str] = None) -> bool:
    s = value.strip().lower()
    if s in _PLACEHOLDER_TERMS:
        return True
    if node_type and s == node_type.strip().lower():
        return True
    return False


async def _get_or_create_entity_node(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    resolved_term: dict,
    fallback_type: str,
    entry_uuid: str,
    ts: str,
    user_id: str,
) -> tuple[bool, Node]:
    existing_uuid = resolved_term.get("existing_uuid")
    if existing_uuid:
        ref = db.collection(config.lethe_collection).document(existing_uuid)
        snap = await ref.get()
        if not snap.exists and resolved_term.get("self_token"):
            node = await ensure_node(
                db=db,
                embedder=embedder,
                config=config,
                identifier="SELF",
                node_type="person",
                source_entry_id=entry_uuid,
                timestamp=ts,
                user_id=user_id,
                llm=llm,
            )
            return False, node
        elif not snap.exists:
            node = await ensure_node(
                db=db,
                embedder=embedder,
                config=config,
                identifier=resolved_term["text"],
                node_type=fallback_type,
                source_entry_id=entry_uuid,
                timestamp=ts,
                user_id=user_id,
                llm=llm,
            )
            return False, node
        await ref.update(
            {
                "journal_entry_ids": ArrayUnion([entry_uuid]),
                "updated_at": ts,
            }
        )
        snap = await ref.get()
        data = snap.to_dict() or {}
        node = Node(
            uuid=existing_uuid,
            node_type=data.get("node_type", fallback_type),
            content=(data.get("content") or resolved_term["text"]),
            domain=data.get("domain", "entity"),
            weight=float(
                data.get("weight", data.get("significance_weight", DEFAULT_ENTITY_WEIGHT))
            ),
            metadata=data.get("metadata", "{}"),
            journal_entry_ids=list(data.get("journal_entry_ids", [])),
            name_key=data.get("name_key"),
            user_id=data.get("user_id", user_id),
            source=data.get("source"),
        )
        return True, node

    exists = await _node_exists(db, config, fallback_type, resolved_term["text"])
    node = await ensure_node(
        db=db,
        embedder=embedder,
        config=config,
        identifier=resolved_term["text"],
        node_type=fallback_type,
        source_entry_id=entry_uuid,
        timestamp=ts,
        user_id=user_id,
        llm=llm,
    )
    return exists, node


def _track(node_uuid: str, existed: bool, created: list, updated: list) -> None:
    if not existed and node_uuid not in created:
        created.append(node_uuid)
    elif existed and node_uuid not in updated and node_uuid not in created:
        updated.append(node_uuid)
