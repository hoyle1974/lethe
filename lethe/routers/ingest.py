from __future__ import annotations

from fastapi import APIRouter, Depends
from google.cloud import firestore

from lethe.config import Config
from lethe.deps import get_canonical_map, get_config, get_db, get_embedder, get_llm
from lethe.graph.canonical_map import CanonicalMap
from lethe.graph.corpus import run_corpus_ingest
from lethe.graph.ingest import run_ingest
from lethe.infra.embedder import Embedder
from lethe.infra.llm import LLMDispatcher
from lethe.models.node import (
    CorpusIngestRequest,
    CorpusIngestResponse,
    IngestRequest,
    IngestResponse,
)

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


@router.post("/v1/ingest/corpus", response_model=CorpusIngestResponse, status_code=201)
async def ingest_corpus(
    req: CorpusIngestRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    llm: LLMDispatcher = Depends(get_llm),
    config: Config = Depends(get_config),
    canonical_map: CanonicalMap = Depends(get_canonical_map),
):
    return await run_corpus_ingest(
        db=db,
        embedder=embedder,
        llm=llm,
        config=config,
        canonical_map=canonical_map,
        documents=req.documents,
        corpus_id=req.corpus_id,
        user_id=req.user_id,
        domain=req.domain,
        chunk_size=req.chunk_size,
    )
