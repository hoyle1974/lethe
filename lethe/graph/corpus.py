from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone

import httpx
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
from lethe.models.node import (
    CorpusDocumentRequest,
    CorpusIngestResponse,
    DocumentItem,
    IngestResponse,
)

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
            await (
                db.collection(config.lethe_collection)
                .document(doc_id)
                .update({"pipeline_done_at": ts})
            )
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

    summary_entities: list[tuple[str, str]] = []
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

        # Task 1: document → summary-log structural edge
        hs_rel_id = await create_relationship_node(
            db=db,
            embedder=embedder,
            config=config,
            subject_id=doc_id,
            predicate="has_summary",
            object_id=summary_result.entry_uuid,
            source_entry_id=doc_id,
            subject_content=f"document {doc.filename}",
            object_content="summary log",
            timestamp=ts,
            user_id=user_id,
            llm=None,
        )
        if hs_rel_id not in seen_relationships:
            seen_relationships.add(hs_rel_id)
            relationships_created.append(hs_rel_id)

        # Task 2: fetch entity nodes produced by summary ingest for lexical linking
        entity_ids = list(set(summary_result.nodes_created + summary_result.nodes_updated))
        if entity_ids:
            refs = [db.collection(config.lethe_collection).document(eid) for eid in entity_ids]
            try:
                async for snap in db.get_all(refs):
                    data = snap.to_dict() or {}
                    if data.get("node_type") != "log":
                        content = data.get("content") or ""
                        if content:
                            summary_entities.append((snap.id, content))
            except Exception:
                log.warning("corpus: get_all for entity linking failed doc_id=%s", doc_id)

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
    prev_chunk_id: str | None = None
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

        # Task 3a: chain consecutive chunks
        if prev_chunk_id is not None:
            nc_rel_id = await create_relationship_node(
                db=db,
                embedder=embedder,
                config=config,
                subject_id=prev_chunk_id,
                predicate="next_chunk",
                object_id=chunk_id,
                source_entry_id=doc_id,
                subject_content=f"chunk {i - 1} of {doc.filename}",
                object_content=f"chunk {i} of {doc.filename}",
                timestamp=ts,
                user_id=user_id,
                llm=None,
            )
            if nc_rel_id not in seen_relationships:
                seen_relationships.add(nc_rel_id)
                relationships_created.append(nc_rel_id)

        # Task 3b: link entities whose name appears in this chunk
        for entity_id, entity_content in summary_entities:
            if re.search(rf"\b{re.escape(entity_content)}\b", chunk_text, re.IGNORECASE):
                mi_rel_id = await create_relationship_node(
                    db=db,
                    embedder=embedder,
                    config=config,
                    subject_id=entity_id,
                    predicate="mentioned_in",
                    object_id=chunk_id,
                    source_entry_id=doc_id,
                    subject_content=entity_content,
                    object_content=f"chunk {i} of {doc.filename}",
                    timestamp=ts,
                    user_id=user_id,
                    llm=None,
                )
                if mi_rel_id not in seen_relationships:
                    seen_relationships.add(mi_rel_id)
                    relationships_created.append(mi_rel_id)

        prev_chunk_id = chunk_id

    has_chunk_rel_ids: list[str] = await asyncio.gather(
        *[
            create_relationship_node(
                db=db,
                embedder=embedder,
                config=config,
                subject_id=doc_id,
                predicate="has_chunk",
                object_id=chunk_id,
                source_entry_id=doc_id,
                subject_content=f"document {doc.filename}",
                object_content=f"chunk {i} of {doc.filename}",
                timestamp=ts,
                user_id=user_id,
                llm=None,
            )
            for i, chunk_id in enumerate(chunk_ids)
        ]
    )
    for rel_id in has_chunk_rel_ids:
        if rel_id not in seen_relationships:
            seen_relationships.add(rel_id)
            relationships_created.append(rel_id)

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

    await db.collection(config.lethe_collection).document(doc_id).update({"pipeline_done_at": ts})

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


# ---------------------------------------------------------------------------
# Fan-out support: Phase 1 setup + single-document pipeline + Cloud Run calls
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CorpusSetup:
    corpus_node_id: str
    corpus_is_new: bool
    doc_classifications: list[tuple[str, bool, bool]]  # (doc_id, is_new, is_changed)


async def run_corpus_setup(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    corpus_id: str,
    documents: list[DocumentItem],
    user_id: str,
    domain: str,
    ts: str,
) -> CorpusSetup:
    """Phase 1: upsert corpus hub node + classify all documents. No LLM calls."""
    filenames = [d.filename for d in documents]
    corpus_result, *doc_results = await asyncio.gather(
        _upsert_corpus_node(db, embedder, config, corpus_id, filenames, user_id, domain, ts),
        *[
            _upsert_document_node(
                db, embedder, config, d.text, d.filename, corpus_id, user_id, domain, ts
            )
            for d in documents
        ],
    )
    return CorpusSetup(
        corpus_node_id=corpus_result[0],
        corpus_is_new=corpus_result[1],
        doc_classifications=list(doc_results),
    )


async def run_single_document_pipeline(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    canonical_map: CanonicalMap,
    req: CorpusDocumentRequest,
) -> _DocPipelineResult:
    """Process one document through the full pipeline (used by the fan-out endpoint)."""
    return await _process_document_pipeline(
        db=db,
        embedder=embedder,
        llm=llm,
        config=config,
        canonical_map=canonical_map,
        doc=req.doc,
        doc_id=req.doc_id,
        is_new=req.is_new,
        is_changed=True,
        corpus_id=req.corpus_id,
        corpus_node_id=req.corpus_node_id,
        user_id=req.user_id,
        domain=req.domain,
        chunk_size=req.chunk_size,
        ts=req.ts,
        doc_idx=req.doc_idx,
        total_docs=req.total_docs,
    )


async def _get_identity_token(audience: str) -> str:
    """Fetch a Cloud Run identity token from the GCE metadata server."""
    url = (
        "http://metadata.google.internal/computeMetadata/v1"
        f"/instance/service-accounts/default/identity?audience={audience}"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers={"Metadata-Flavor": "Google"}, timeout=10.0)
        resp.raise_for_status()
        return resp.text.strip()


async def fanout_corpus_documents(
    service_url: str,
    doc_requests: list[CorpusDocumentRequest],
) -> None:
    """Fire one authenticated HTTPS call per document to the service's own document endpoint."""
    if not doc_requests:
        return

    try:
        token = await _get_identity_token(service_url)
        auth_header = {"Authorization": f"Bearer {token}"}
    except Exception:
        log.warning("corpus: metadata server unavailable, fan-out calls will be unauthenticated")
        auth_header = {}

    endpoint = f"{service_url.rstrip('/')}/v1/ingest/corpus/document"
    headers = {"Content-Type": "application/json", **auth_header}

    async with httpx.AsyncClient() as client:
        tasks = [
            client.post(endpoint, content=req.model_dump_json(), headers=headers, timeout=600.0)
            for req in doc_requests
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for req, result in zip(doc_requests, results):
        if isinstance(result, Exception):
            log.error("corpus: fan-out failed filename=%r err=%s", req.doc.filename, result)
        elif result.status_code not in (200, 201, 202):
            log.error(
                "corpus: fan-out bad status filename=%r status=%d body=%s",
                req.doc.filename,
                result.status_code,
                result.text[:200],
            )
        else:
            log.info("corpus: fan-out complete filename=%r", req.doc.filename)
