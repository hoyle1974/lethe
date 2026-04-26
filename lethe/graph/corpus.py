from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from google.cloud import firestore

from lethe.config import Config
from lethe.constants import (
    CHUNK_NODE_WEIGHT,
    CORPUS_LLM_CONCURRENCY,
    CORPUS_NODE_WEIGHT,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_DOMAIN,
    DEFAULT_USER_ID,
    DOCUMENT_NODE_WEIGHT,
    EMBEDDING_TASK_RETRIEVAL_DOCUMENT,
    NODE_TYPE_CHUNK,
    NODE_TYPE_CORPUS,
    NODE_TYPE_DOCUMENT,
)
from lethe.graph.canonical_map import CanonicalMap
from lethe.graph.chunk import chunk_document, detect_chunk_strategy
from lethe.graph.code_graph import extract_structural_triples
from lethe.graph.ensure_node import (
    create_relationship_node,
    ensure_node,
    stable_entity_doc_id,
)
from lethe.graph.extraction import summarize_document
from lethe.graph.ingest import run_ingest
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import FieldFilter, Vector
from lethe.infra.llm import LLMDispatcher
from lethe.models.node import CorpusIngestResponse, DocumentItem, IngestResponse

log = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def stable_document_id(corpus_id: str, filename: str) -> str:
    key = f"document:{corpus_id}:{filename}"
    return "doc_" + hashlib.sha1(key.encode()).hexdigest()


def stable_corpus_node_id(corpus_id: str) -> str:
    key = f"corpus:{corpus_id}"
    return "corpus_" + hashlib.sha1(key.encode()).hexdigest()


async def _upsert_document_node(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    text: str,
    filename: str,
    corpus_id: str,
    user_id: str,
    domain: str,
    ts: str,
) -> tuple[str, bool, bool]:
    """Upsert a document node. Returns (doc_id, is_new, is_changed)."""
    doc_id = stable_document_id(corpus_id, filename)
    snap = await db.collection(config.lethe_collection).document(doc_id).get()
    content_hash = _content_hash(text)

    if snap.exists:
        try:
            existing_meta = json.loads(snap.get("metadata") or "{}")
        except (TypeError, ValueError):
            existing_meta = {}
        if existing_meta.get("content_hash") == content_hash:
            log.info("corpus: skip unchanged doc_id=%s filename=%r", doc_id, filename)
            return doc_id, False, False

        vector = await embedder.embed(text[:10_000], EMBEDDING_TASK_RETRIEVAL_DOCUMENT)
        metadata = json.dumps(
            {"filename": filename, "corpus_id": corpus_id, "content_hash": content_hash}
        )
        await (
            db.collection(config.lethe_collection)
            .document(doc_id)
            .update(
                {
                    "content": text,
                    "metadata": metadata,
                    "embedding": Vector(vector),
                    "updated_at": ts,
                }
            )
        )
        log.info("corpus: updated document node doc_id=%s filename=%r", doc_id, filename)
        return doc_id, False, True

    vector = await embedder.embed(text[:10_000], EMBEDDING_TASK_RETRIEVAL_DOCUMENT)
    metadata = json.dumps(
        {"filename": filename, "corpus_id": corpus_id, "content_hash": content_hash}
    )
    await (
        db.collection(config.lethe_collection)
        .document(doc_id)
        .set(
            {
                "node_type": NODE_TYPE_DOCUMENT,
                "content": text,
                "domain": domain,
                "weight": DOCUMENT_NODE_WEIGHT,
                "metadata": metadata,
                "embedding": Vector(vector),
                "user_id": user_id,
                "source": corpus_id,
                "created_at": ts,
                "updated_at": ts,
            }
        )
    )
    log.info("corpus: created document node doc_id=%s filename=%r", doc_id, filename)
    return doc_id, True, True


async def _upsert_corpus_node(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    corpus_id: str,
    filenames: list[str],
    user_id: str,
    domain: str,
    ts: str,
) -> tuple[str, bool]:
    """Upsert a corpus hub node. Returns (node_id, is_new)."""
    node_id = stable_corpus_node_id(corpus_id)
    snap = await db.collection(config.lethe_collection).document(node_id).get()
    files_summary = ", ".join(filenames) if filenames else "(no files)"
    content = f"Corpus '{corpus_id}': {files_summary}"
    vector = await embedder.embed(content, EMBEDDING_TASK_RETRIEVAL_DOCUMENT)
    metadata = json.dumps({"corpus_id": corpus_id, "file_count": len(filenames)})

    if snap.exists:
        await (
            db.collection(config.lethe_collection)
            .document(node_id)
            .update(
                {
                    "content": content,
                    "metadata": metadata,
                    "embedding": Vector(vector),
                    "updated_at": ts,
                }
            )
        )
        log.info("corpus: updated corpus node corpus_node_id=%s corpus_id=%r", node_id, corpus_id)
        return node_id, False

    await (
        db.collection(config.lethe_collection)
        .document(node_id)
        .set(
            {
                "node_type": NODE_TYPE_CORPUS,
                "content": content,
                "domain": domain,
                "weight": CORPUS_NODE_WEIGHT,
                "metadata": metadata,
                "embedding": Vector(vector),
                "user_id": user_id,
                "source": corpus_id,
                "created_at": ts,
                "updated_at": ts,
            }
        )
    )
    log.info("corpus: created corpus node corpus_node_id=%s corpus_id=%r", node_id, corpus_id)
    return node_id, True


async def _create_chunk_node(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    text: str,
    document_id: str,
    corpus_id: str,
    filename: str,
    chunk_index: int,
    user_id: str,
    domain: str,
    ts: str,
) -> str:
    chunk_id = str(uuid.uuid4())
    vector = await embedder.embed(text[:10_000], EMBEDDING_TASK_RETRIEVAL_DOCUMENT)
    metadata = json.dumps(
        {
            "document_id": document_id,
            "corpus_id": corpus_id,
            "filename": filename,
            "chunk_index": chunk_index,
        }
    )
    await (
        db.collection(config.lethe_collection)
        .document(chunk_id)
        .set(
            {
                "node_type": NODE_TYPE_CHUNK,
                "content": text,
                "domain": domain,
                "weight": CHUNK_NODE_WEIGHT,
                "metadata": metadata,
                "document_id": document_id,
                "embedding": Vector(vector),
                "user_id": user_id,
                "source": corpus_id,
                "created_at": ts,
                "updated_at": ts,
            }
        )
    )
    log.info(
        "corpus: created chunk node chunk_id=%s filename=%r chunk_index=%d",
        chunk_id,
        filename,
        chunk_index,
    )
    return chunk_id


async def _tombstone_chunks_for_document(
    db: firestore.AsyncClient,
    config: Config,
    document_id: str,
    user_id: str,
) -> None:
    col = db.collection(config.lethe_collection)
    q = col.where(filter=FieldFilter("document_id", "==", document_id)).where(
        filter=FieldFilter("user_id", "==", user_id)
    )
    snaps = await q.get()
    for snap in snaps:
        await col.document(snap.id).update({"weight": 0.0})
    log.info("corpus: tombstoned %d chunks for document_id=%s", len(snaps), document_id)


async def _get_existing_chunk_ids(
    db: firestore.AsyncClient,
    config: Config,
    document_id: str,
    user_id: str,
) -> list[str]:
    col = db.collection(config.lethe_collection)
    q = col.where(filter=FieldFilter("document_id", "==", document_id)).where(
        filter=FieldFilter("user_id", "==", user_id)
    )
    snaps = await q.get()
    return [snap.id for snap in snaps if (snap.get("weight") or 0.0) > 0.0]


async def _node_exists_by_type(
    db: firestore.AsyncClient,
    config: Config,
    node_type: str,
    name: str,
) -> bool:
    doc_id = stable_entity_doc_id(node_type, name)
    snap = await db.collection(config.lethe_collection).document(doc_id).get()
    return snap.exists


def _merge_ingest_result(
    result: IngestResponse,
    seen_created: set[str],
    seen_updated: set[str],
    seen_relationships: set[str],
    all_nodes_created: list[str],
    all_nodes_updated: list[str],
    all_relationships_created: list[str],
) -> None:
    for n in result.nodes_created:
        if n not in seen_created:
            seen_created.add(n)
            seen_updated.discard(n)
            all_nodes_created.append(n)
    for n in result.nodes_updated:
        if n not in seen_created and n not in seen_updated:
            seen_updated.add(n)
            all_nodes_updated.append(n)
    for r in result.relationships_created:
        if r not in seen_relationships:
            seen_relationships.add(r)
            all_relationships_created.append(r)


_STRUCTURAL_PREDICATE_OBJECT_TYPE: dict[str, str] = {
    "imports": "module",
    "defines": "function",
    "has_method": "function",
}


async def _ingest_structural_edges(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    text: str,
    filename: str,
    document_id: str,
    corpus_id: str,
    user_id: str,
    domain: str,
    ts: str,
) -> tuple[list[str], list[str], list[str]]:
    """Write deterministic code-structure edges. Returns (nodes_created, nodes_updated, rels)."""
    triples = extract_structural_triples(text, filename)
    if not triples:
        return [], [], []

    nodes_created: list[str] = []
    nodes_updated: list[str] = []
    relationships_created: list[str] = []
    seen_created: set[str] = set()
    seen_updated: set[str] = set()
    seen_relationships: set[str] = set()

    log.info("corpus: structural edges filename=%r triples=%d", filename, len(triples))
    for subj, pred, obj in triples:
        subj_type = "module"
        obj_type = _STRUCTURAL_PREDICATE_OBJECT_TYPE.get(pred, "generic")

        subj_existed = await _node_exists_by_type(db, config, subj_type, subj)
        subj_node = await ensure_node(
            db=db,
            embedder=embedder,
            config=config,
            identifier=subj,
            node_type=subj_type,
            source_entry_id=document_id,
            timestamp=ts,
            user_id=user_id,
            llm=None,
        )

        obj_existed = await _node_exists_by_type(db, config, obj_type, obj)
        obj_node = await ensure_node(
            db=db,
            embedder=embedder,
            config=config,
            identifier=obj,
            node_type=obj_type,
            source_entry_id=document_id,
            timestamp=ts,
            user_id=user_id,
            llm=None,
        )

        for node_uuid, existed in [
            (subj_node.uuid, subj_existed),
            (obj_node.uuid, obj_existed),
        ]:
            if not existed and node_uuid not in seen_created:
                seen_created.add(node_uuid)
                seen_updated.discard(node_uuid)
                nodes_created.append(node_uuid)
            elif existed and node_uuid not in seen_created and node_uuid not in seen_updated:
                seen_updated.add(node_uuid)
                nodes_updated.append(node_uuid)

        rel_id = await create_relationship_node(
            db=db,
            embedder=embedder,
            config=config,
            subject_id=subj_node.uuid,
            predicate=pred,
            object_id=obj_node.uuid,
            source_entry_id=document_id,
            subject_content=subj_node.content,
            object_content=obj_node.content,
            timestamp=ts,
            user_id=user_id,
            llm=None,
        )
        if rel_id not in seen_relationships:
            seen_relationships.add(rel_id)
            relationships_created.append(rel_id)

    return nodes_created, nodes_updated, relationships_created


@dataclasses.dataclass
class _DocPipelineResult:
    doc_id: str
    chunk_ids: list[str]
    nodes_created: list[str]
    nodes_updated: list[str]
    relationships_created: list[str]


class _RateLimitedLLM:
    """LLM wrapper that limits concurrent dispatches via a semaphore.

    All document pipelines run fully in parallel; only the LLM calls
    (summarize + extract_triples) are throttled at this layer.
    """

    def __init__(self, llm: LLMDispatcher, semaphore: asyncio.Semaphore) -> None:
        self._llm = llm
        self._sem = semaphore

    async def dispatch(self, req: object) -> str:
        async with self._sem:
            return await self._llm.dispatch(req)  # type: ignore[arg-type]


async def _process_document_pipeline(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    canonical_map: CanonicalMap,
    doc: DocumentItem,
    doc_id: str,
    is_new: bool,
    is_changed: bool,
    corpus_id: str,
    corpus_node_id: str,
    user_id: str,
    domain: str,
    chunk_size: int,
    ts: str,
    doc_idx: int,
    total_docs: int,
) -> _DocPipelineResult:
    if not is_changed:
        existing = await _get_existing_chunk_ids(db, config, doc_id, user_id)
        log.info(
            "corpus: [%d/%d] skip unchanged %r (doc_id=%s chunks=%d)",
            doc_idx + 1,
            total_docs,
            doc.filename,
            doc_id,
            len(existing),
        )
        return _DocPipelineResult(
            doc_id=doc_id,
            chunk_ids=existing,
            nodes_created=[],
            nodes_updated=[],
            relationships_created=[],
        )

    nodes_created: list[str] = []
    nodes_updated: list[str] = []
    relationships_created: list[str] = []
    seen_created: set[str] = set()
    seen_updated: set[str] = set()
    seen_relationships: set[str] = set()

    log.info("corpus: [%d/%d] starting %r", doc_idx + 1, total_docs, doc.filename)

    if not is_new:
        seen_updated.add(doc_id)
        nodes_updated.append(doc_id)
        await _tombstone_chunks_for_document(db, config, doc_id, user_id)
    else:
        seen_created.add(doc_id)
        nodes_created.append(doc_id)
        rel_id = await create_relationship_node(
            db=db,
            embedder=embedder,
            config=config,
            subject_id=corpus_node_id,
            predicate="contains",
            object_id=doc_id,
            source_entry_id=corpus_node_id,
            subject_content=f"Corpus '{corpus_id}'",
            object_content=f"document {doc.filename}",
            timestamp=ts,
            user_id=user_id,
            llm=None,
        )
        if rel_id not in seen_relationships:
            seen_relationships.add(rel_id)
            relationships_created.append(rel_id)

    summary = await summarize_document(llm=llm, text=doc.text, filename=doc.filename)
    log.info(
        "corpus: [%d/%d] summary=%d chars filename=%r",
        doc_idx + 1,
        total_docs,
        len(summary),
        doc.filename,
    )
    if summary:
        summary_result = await run_ingest(
            db=db,
            embedder=embedder,
            llm=llm,
            config=config,
            canonical_map=canonical_map,
            text=summary,
            domain=domain,
            source=corpus_id,
            user_id=user_id,
            timestamp=ts,
            metadata={
                "document_id": doc_id,
                "filename": doc.filename,
                "is_summary": True,
            },
        )
        _merge_ingest_result(
            summary_result,
            seen_created,
            seen_updated,
            seen_relationships,
            nodes_created,
            nodes_updated,
            relationships_created,
        )

    chunks = chunk_document(doc.text, doc.filename, chunk_size)
    log.info(
        "corpus: [%d/%d] %r → %d chunks (doc_id=%s)",
        doc_idx + 1,
        total_docs,
        doc.filename,
        len(chunks),
        doc_id,
    )
    chunk_ids: list[str] = []
    for i, chunk_text in enumerate(chunks):
        chunk_id = await _create_chunk_node(
            db=db,
            embedder=embedder,
            config=config,
            text=chunk_text,
            document_id=doc_id,
            corpus_id=corpus_id,
            filename=doc.filename,
            chunk_index=i,
            user_id=user_id,
            domain=domain,
            ts=ts,
        )
        chunk_ids.append(chunk_id)

    if detect_chunk_strategy(doc.filename) == "code":
        struct_created, struct_updated, struct_rels = await _ingest_structural_edges(
            db=db,
            embedder=embedder,
            config=config,
            text=doc.text,
            filename=doc.filename,
            document_id=doc_id,
            corpus_id=corpus_id,
            user_id=user_id,
            domain=domain,
            ts=ts,
        )
        for n in struct_created:
            if n not in seen_created:
                seen_created.add(n)
                nodes_created.append(n)
        for n in struct_updated:
            if n not in seen_created and n not in seen_updated:
                seen_updated.add(n)
                nodes_updated.append(n)
        for r in struct_rels:
            if r not in seen_relationships:
                seen_relationships.add(r)
                relationships_created.append(r)

    return _DocPipelineResult(
        doc_id=doc_id,
        chunk_ids=chunk_ids,
        nodes_created=nodes_created,
        nodes_updated=nodes_updated,
        relationships_created=relationships_created,
    )


async def run_corpus_ingest(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    canonical_map: CanonicalMap,
    documents: list[DocumentItem],
    corpus_id: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    domain: str = DEFAULT_DOMAIN,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> CorpusIngestResponse:
    corpus_id = corpus_id or str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()

    filenames = [doc.filename for doc in documents]

    # Phase 1: upsert corpus node + classify all documents in parallel.
    # _upsert_document_node is fast (one Firestore get + maybe embed); no LLM.
    corpus_result, *doc_classifications = await asyncio.gather(
        _upsert_corpus_node(
            db=db,
            embedder=embedder,
            config=config,
            corpus_id=corpus_id,
            filenames=filenames,
            user_id=user_id,
            domain=domain,
            ts=ts,
        ),
        *[
            _upsert_document_node(
                db=db,
                embedder=embedder,
                config=config,
                text=doc.text,
                filename=doc.filename,
                corpus_id=corpus_id,
                user_id=user_id,
                domain=domain,
                ts=ts,
            )
            for doc in documents
        ],
    )
    corpus_node_id, corpus_is_new = corpus_result

    # Phase 2: all document pipelines run fully in parallel.
    # LLM calls inside each pipeline are throttled via _RateLimitedLLM so
    # at most CORPUS_LLM_CONCURRENCY generate_content requests are in flight
    # at any time. Embedding and Firestore writes are unrestricted.
    rate_limited_llm = _RateLimitedLLM(llm, asyncio.Semaphore(CORPUS_LLM_CONCURRENCY))
    doc_results: list[_DocPipelineResult] = await asyncio.gather(
        *[
            _process_document_pipeline(
                db=db,
                embedder=embedder,
                llm=rate_limited_llm,
                config=config,
                canonical_map=canonical_map,
                doc=doc,
                doc_id=doc_id,
                is_new=is_new,
                is_changed=is_changed,
                corpus_id=corpus_id,
                corpus_node_id=corpus_node_id,
                user_id=user_id,
                domain=domain,
                chunk_size=chunk_size,
                ts=ts,
                doc_idx=doc_idx,
                total_docs=len(documents),
            )
            for doc_idx, (doc, (doc_id, is_new, is_changed)) in enumerate(
                zip(documents, doc_classifications)
            )
        ]
    )

    # Aggregate results with global deduplication.
    document_ids: list[str] = []
    chunk_ids: list[str] = []
    all_nodes_created: list[str] = []
    all_nodes_updated: list[str] = []
    all_relationships_created: list[str] = []
    seen_created: set[str] = set()
    seen_updated: set[str] = set()
    seen_relationships: set[str] = set()

    if corpus_is_new:
        seen_created.add(corpus_node_id)
        all_nodes_created.append(corpus_node_id)
    else:
        seen_updated.add(corpus_node_id)
        all_nodes_updated.append(corpus_node_id)

    for result in doc_results:
        document_ids.append(result.doc_id)
        chunk_ids.extend(result.chunk_ids)
        for n in result.nodes_created:
            if n not in seen_created:
                seen_created.add(n)
                seen_updated.discard(n)
                all_nodes_created.append(n)
        for n in result.nodes_updated:
            if n not in seen_created and n not in seen_updated:
                seen_updated.add(n)
                all_nodes_updated.append(n)
        for r in result.relationships_created:
            if r not in seen_relationships:
                seen_relationships.add(r)
                all_relationships_created.append(r)

    total_chunks = len(chunk_ids)
    log.info(
        "corpus: complete corpus_id=%s documents=%d chunks=%d nodes_created=%d",
        corpus_id,
        len(document_ids),
        total_chunks,
        len(all_nodes_created),
    )
    return CorpusIngestResponse(
        corpus_id=corpus_id,
        corpus_node_id=corpus_node_id,
        document_ids=document_ids,
        chunk_ids=chunk_ids,
        total_chunks=total_chunks,
        nodes_created=all_nodes_created,
        nodes_updated=all_nodes_updated,
        relationships_created=all_relationships_created,
    )
