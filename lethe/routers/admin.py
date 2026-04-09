from fastapi import APIRouter

router = APIRouter()


@router.get("/v1/health")
async def health():
    return {"status": "ok"}
