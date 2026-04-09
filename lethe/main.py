import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from lethe.config import Config
from lethe.infra.firestore import create_firestore_client
from lethe.infra.gemini import GeminiEmbedder, GeminiLLM
from lethe.graph.canonical_map import seed_canonical_map, load_canonical_map
from lethe.routers import admin
from lethe.routers import ingest as ingest_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config()
    app.state.config = config
    app.state.db = create_firestore_client(config)
    app.state.embedder = GeminiEmbedder(config)
    app.state.llm = GeminiLLM(config)
    await seed_canonical_map(app.state.db)
    app.state.canonical_map = await load_canonical_map(app.state.db)
    logging.basicConfig(level=config.log_level.upper())
    yield


app = FastAPI(title="Lethe", lifespan=lifespan)
app.include_router(admin.router)
app.include_router(ingest_router.router)
