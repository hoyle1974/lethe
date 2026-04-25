from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from google.cloud import firestore

from lethe.config import Config
from lethe.constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_DOMAIN,
    DEFAULT_USER_ID,
    DOCUMENT_NODE_WEIGHT,
    EMBEDDING_TASK_RETRIEVAL_DOCUMENT,
    NODE_TYPE_DOCUMENT,
)
from lethe.graph.canonical_map import CanonicalMap
from lethe.graph.chunk import chunk_document
from lethe.graph.ingest import run_ingest
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import Vector
from lethe.infra.llm import LLMDispatcher
from lethe.models.node import CorpusIngestResponse, DocumentItem

log = logging.getLogger(__name__)


async def _create_document_node(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    text: str,
    filename: str,
    corpus_id: str,
    user_id: str,
    domain: str,
    ts: str,
) -> str:
    doc_id = str(uuid.uuid4())
    vector = await embedder.embed(text[:10_000], EMBEDDING_TASK_RETRIEVAL_DOCUMENT)
    metadata = json.dumps({"filename": filename, "corpus_id": corpus_id})
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
    return doc_id


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

    document_ids: list[str] = []
    all_nodes_created: list[str] = []
    all_nodes_updated: list[str] = []
    all_relationships_created: list[str] = []
    seen_created: set[str] = set()
    seen_updated: set[str] = set()
    seen_relationships: set[str] = set()
    total_chunks = 0

    for doc in documents:
        doc_id = await _create_document_node(
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
        document_ids.append(doc_id)

        chunks = chunk_document(doc.text, doc.filename, chunk_size)
        log.info(
            "corpus: document doc_id=%s filename=%r split into %d chunks",
            doc_id,
            doc.filename,
            len(chunks),
        )

        for i, chunk_text in enumerate(chunks):
            result = await run_ingest(
                db=db,
                embedder=embedder,
                llm=llm,
                config=config,
                canonical_map=canonical_map,
                text=chunk_text,
                domain=domain,
                source=corpus_id,
                user_id=user_id,
                timestamp=ts,
                metadata={"document_id": doc_id, "chunk_index": i, "filename": doc.filename},
            )
            total_chunks += 1
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

    log.info(
        "corpus: complete corpus_id=%s documents=%d chunks=%d nodes_created=%d",
        corpus_id,
        len(document_ids),
        total_chunks,
        len(all_nodes_created),
    )
    return CorpusIngestResponse(
        corpus_id=corpus_id,
        document_ids=document_ids,
        total_chunks=total_chunks,
        nodes_created=all_nodes_created,
        nodes_updated=all_nodes_updated,
        relationships_created=all_relationships_created,
    )
