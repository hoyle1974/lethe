from fastapi import APIRouter, Depends

from lethe.config import Config
from lethe.deps import get_canonical_map, get_config, get_db, get_embedder, get_llm
from lethe.graph.canonical_map import CanonicalMap
from lethe.graph.ingest import run_ingest
from lethe.models.node import IngestRequest, IngestResponse

router = APIRouter()


@router.post("/v1/ingest", response_model=IngestResponse)
async def ingest(
    req: IngestRequest,
    db=Depends(get_db),
    embedder=Depends(get_embedder),
    llm=Depends(get_llm),
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
