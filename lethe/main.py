import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from lethe.config import Config
from lethe.routers import admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config()
    app.state.config = config
    app.state.db = None
    app.state.embedder = None
    app.state.llm = None
    app.state.canonical_map = None
    logging.basicConfig(level=config.log_level.upper())
    yield


app = FastAPI(title="Lethe", lifespan=lifespan)
app.include_router(admin.router)
