from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends
from google.cloud import firestore

from lethe.config import Config
from lethe.deps import get_canonical_map, get_config, get_db, get_embedder, get_llm
from lethe.graph.canonical_map import CanonicalMap
from lethe.graph.corpus import (
    fanout_corpus_documents,
    run_corpus_ingest,
    run_corpus_setup,
    run_single_document_pipeline,
    stable_corpus_node_id,
    stable_document_id,
)
from lethe.graph.ingest import run_ingest
from lethe.infra.embedder import Embedder
from lethe.infra.llm import LLMDispatcher
from lethe.models.node import (
    CorpusDocumentRequest,
    CorpusDocumentResponse,
    CorpusIngestRequest,
    CorpusIngestResponse,
    CorpusStatusRequest,
    CorpusStatusResponse,
    IngestRequest,
    IngestResponse,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/ingest", response_model=IngestResponse, status_code=201)
async def ingest(
    req: IngestRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    llm: LLMDispatcher = Depends(get_llm),
    config: Config = Depends(get_config),
    canonical_map: CanonicalMap = Depends(get_canonical_map),
):
    ts = req.timestamp.isoformat() if req.timestamp else None
    return await run_ingest(
        db=db,
        embedder=embedder,
        llm=llm,
        config=config,
        canonical_map=canonical_map,
        text=req.text,
        domain=req.domain,
        source=req.source,
        user_id=req.user_id,
        timestamp=ts,
    )


async def _run_corpus_inprocess(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    canonical_map: CanonicalMap,
    corpus_id: str,
    req: CorpusIngestRequest,
) -> None:
    try:
        await run_corpus_ingest(
            db=db,
            embedder=embedder,
            llm=llm,
            config=config,
            canonical_map=canonical_map,
            documents=req.documents,
            corpus_id=corpus_id,
            user_id=req.user_id,
            domain=req.domain,
            chunk_size=req.chunk_size,
        )
    except Exception:
        log.exception("corpus: in-process background ingest failed corpus_id=%s", corpus_id)


async def _run_corpus_fanout(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    service_url: str,
    corpus_id: str,
    req: CorpusIngestRequest,
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    try:
        setup = await run_corpus_setup(
            db=db,
            embedder=embedder,
            config=config,
            corpus_id=corpus_id,
            documents=req.documents,
            user_id=req.user_id,
            domain=req.domain,
            ts=ts,
        )
    except Exception:
        log.exception("corpus: fan-out Phase 1 failed corpus_id=%s", corpus_id)
        return

    doc_requests = [
        CorpusDocumentRequest(
            corpus_id=corpus_id,
            corpus_node_id=setup.corpus_node_id,
            doc_id=doc_id,
            doc=doc,
            is_new=is_new,
            user_id=req.user_id,
            domain=req.domain,
            chunk_size=req.chunk_size,
            ts=ts,
            doc_idx=idx,
            total_docs=len(req.documents),
        )
        for idx, (doc, (doc_id, is_new, is_changed)) in enumerate(
            zip(req.documents, setup.doc_classifications)
        )
        if is_changed
    ]

    log.info(
        "corpus: fanning out %d/%d documents corpus_id=%s",
        len(doc_requests),
        len(req.documents),
        corpus_id,
    )
    try:
        await fanout_corpus_documents(service_url, doc_requests)
    except Exception:
        log.exception("corpus: fan-out Phase 2 failed corpus_id=%s", corpus_id)


@router.post("/v1/ingest/corpus", response_model=CorpusIngestResponse, status_code=202)
async def ingest_corpus(
    req: CorpusIngestRequest,
    background_tasks: BackgroundTasks,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    llm: LLMDispatcher = Depends(get_llm),
    config: Config = Depends(get_config),
    canonical_map: CanonicalMap = Depends(get_canonical_map),
) -> CorpusIngestResponse:
    corpus_id = req.corpus_id or str(uuid.uuid4())
    ingest_ts = datetime.now(timezone.utc).isoformat()
    corpus_node_id = stable_corpus_node_id(corpus_id)
    document_ids = [stable_document_id(corpus_id, doc.filename) for doc in req.documents]

    if config.lethe_service_url:
        # Fan-out: Phase 1 runs in background, then one Cloud Run call per document.
        background_tasks.add_task(
            _run_corpus_fanout,
            db=db,
            embedder=embedder,
            config=config,
            service_url=config.lethe_service_url,
            corpus_id=corpus_id,
            req=req,
        )
    else:
        # In-process: entire pipeline runs in background on the same instance.
        background_tasks.add_task(
            _run_corpus_inprocess,
            db=db,
            embedder=embedder,
            llm=llm,
            config=config,
            canonical_map=canonical_map,
            corpus_id=corpus_id,
            req=req,
        )

    return CorpusIngestResponse(
        corpus_id=corpus_id,
        corpus_node_id=corpus_node_id,
        document_ids=document_ids,
        ingest_ts=ingest_ts,
    )


@router.post("/v1/ingest/corpus/document", response_model=CorpusDocumentResponse, status_code=201)
async def ingest_corpus_document(
    req: CorpusDocumentRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    llm: LLMDispatcher = Depends(get_llm),
    config: Config = Depends(get_config),
    canonical_map: CanonicalMap = Depends(get_canonical_map),
) -> CorpusDocumentResponse:
    result = await run_single_document_pipeline(
        db=db,
        embedder=embedder,
        llm=llm,
        config=config,
        canonical_map=canonical_map,
        req=req,
    )
    return CorpusDocumentResponse(
        doc_id=result.doc_id,
        chunk_ids=result.chunk_ids,
        nodes_created=result.nodes_created,
        nodes_updated=result.nodes_updated,
        relationships_created=result.relationships_created,
    )


@router.post(
    "/v1/ingest/corpus/{corpus_id}/status",
    response_model=CorpusStatusResponse,
    status_code=200,
)
async def corpus_ingest_status(
    corpus_id: str,
    req: CorpusStatusRequest,
    db: firestore.AsyncClient = Depends(get_db),
    config: Config = Depends(get_config),
) -> CorpusStatusResponse:
    """Return how many documents in this ingest run have finished their pipeline."""
    if not req.document_ids:
        return CorpusStatusResponse(corpus_id=corpus_id, total=0, completed=0, is_complete=True)

    # Deduplicate: same-basename files produce the same stable_document_id; counting
    # duplicates inflates total while get_all deduplicates, causing is_complete to
    # never be true.
    unique_ids = list(dict.fromkeys(req.document_ids))
    refs = [db.collection(config.lethe_collection).document(doc_id) for doc_id in unique_ids]
    completed = 0
    failed = 0
    async for snap in db.get_all(refs):
        data = (snap.to_dict() or {}) if snap.exists else {}
        done_at = data.get("pipeline_done_at")
        if done_at and (not req.ingest_ts or done_at >= req.ingest_ts):
            if data.get("pipeline_error"):
                failed += 1
            else:
                completed += 1

    total = len(unique_ids)
    return CorpusStatusResponse(
        corpus_id=corpus_id,
        total=total,
        completed=completed,
        failed=failed,
        is_complete=(completed + failed) >= total,
    )
