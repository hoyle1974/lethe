from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

from google.cloud import firestore
from lethe.infra.fs_helpers import Vector

from lethe.config import Config
from lethe.graph.canonical_map import CanonicalMap, append_predicate
from lethe.graph.ensure_node import (
    ensure_node, create_relationship_node, add_entity_link, update_hot_edges,
    stable_entity_doc_id,
)
from lethe.graph.extraction import extract_triples, RefineryTriple
from lethe.infra.embedder import Embedder
from lethe.infra.llm import LLMDispatcher
from lethe.models.node import IngestResponse


async def run_ingest(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    canonical_map: CanonicalMap,
    text: str,
    domain: str = "general",
    source: Optional[str] = None,
    user_id: str = "global",
    timestamp: Optional[str] = None,
) -> IngestResponse:
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    entry_uuid = str(uuid.uuid4())

    # Step 1: store episodic log entry
    vector = await embedder.embed(text, "RETRIEVAL_DOCUMENT")
    col = db.collection(config.lethe_collection)
    await col.document(entry_uuid).set({
        "node_type": "log",
        "content": text,
        "domain": domain,
        "weight": 0.3,
        "metadata": "{}",
        "embedding": Vector(vector),
        "entity_links": [],
        "user_id": user_id,
        "source": source,
        "created_at": ts,
        "updated_at": ts,
    })

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
        log.info("ingest: triple subject=%r predicate=%r object=%r",
                 t.subject, t.canonical_predicate, t.object)
    if status == "none" or not triples:
        log.warning("ingest: no triples extracted — entry_uuid=%s", entry_uuid)
        return IngestResponse(entry_uuid=entry_uuid)

    nodes_created: list[str] = []
    nodes_updated: list[str] = []
    relationships_created: list[str] = []

    # Step 3: resolve and commit each triple
    for triple in triples:
        try:
            await _process_triple(
                db=db, embedder=embedder, llm=llm, config=config,
                triple=triple, entry_uuid=entry_uuid, ts=ts, user_id=user_id,
                nodes_created=nodes_created, nodes_updated=nodes_updated,
                relationships_created=relationships_created,
                canonical_map=canonical_map,
            )
        except Exception as e:
            log.error("ingest: _process_triple failed for %r: %s", triple, e, exc_info=True)
            continue

    log.info("ingest: complete entry_uuid=%s nodes_created=%d nodes_updated=%d relationships=%d",
             entry_uuid, len(nodes_created), len(nodes_updated), len(relationships_created))
    return IngestResponse(
        entry_uuid=entry_uuid,
        nodes_created=nodes_created,
        nodes_updated=nodes_updated,
        relationships_created=relationships_created,
    )


async def _process_triple(
    db, embedder, llm, config, triple: RefineryTriple,
    entry_uuid, ts, user_id,
    nodes_created, nodes_updated, relationships_created,
    canonical_map: CanonicalMap,
):
    predicate = triple.canonical_predicate
    if triple.is_new_predicate:
        await append_predicate(db, predicate)
        if predicate not in canonical_map.allowed_predicates:
            canonical_map.allowed_predicates.append(predicate)

    subj_exists = await _node_exists(db, config, triple.subject_type, triple.subject)
    subj_node = await ensure_node(
        db=db, embedder=embedder, config=config,
        identifier=triple.subject, node_type=triple.subject_type,
        source_entry_id=entry_uuid, timestamp=ts, user_id=user_id, llm=llm,
    )

    obj_exists = await _node_exists(db, config, triple.object_type, triple.object)
    obj_node = await ensure_node(
        db=db, embedder=embedder, config=config,
        identifier=triple.object, node_type=triple.object_type,
        source_entry_id=entry_uuid, timestamp=ts, user_id=user_id, llm=llm,
    )

    _track(subj_node.uuid, subj_exists, nodes_created, nodes_updated)
    _track(obj_node.uuid, obj_exists, nodes_created, nodes_updated)

    rel_id = await create_relationship_node(
        db=db, embedder=embedder, config=config,
        subject_id=subj_node.uuid, predicate=predicate, object_id=obj_node.uuid,
        source_entry_id=entry_uuid,
        subject_content=subj_node.content, object_content=obj_node.content,
        timestamp=ts, user_id=user_id,
    )
    if rel_id not in relationships_created:
        relationships_created.append(rel_id)

    await add_entity_link(db, config, subj_node.uuid, rel_id)
    await add_entity_link(db, config, obj_node.uuid, rel_id)
    await add_entity_link(db, config, entry_uuid, rel_id)
    await update_hot_edges(db, config, obj_node.uuid, rel_id)


async def _node_exists(db, config, node_type: str, name: str) -> bool:
    doc_id = stable_entity_doc_id(node_type, name)
    snap = await db.collection(config.lethe_collection).document(doc_id).get()
    return snap.exists


def _track(node_uuid: str, existed: bool, created: list, updated: list) -> None:
    if not existed and node_uuid not in created:
        created.append(node_uuid)
    elif existed and node_uuid not in updated and node_uuid not in created:
        updated.append(node_uuid)
