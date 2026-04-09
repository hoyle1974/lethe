from fastapi import Request
from lethe.config import Config
from lethe.graph.canonical_map import CanonicalMap, load_canonical_map


def get_db(request: Request):
    return request.app.state.db


def get_embedder(request: Request):
    return request.app.state.embedder


def get_llm(request: Request):
    return request.app.state.llm


def get_config(request: Request) -> Config:
    return request.app.state.config


async def get_canonical_map(request: Request) -> CanonicalMap:
    db = request.app.state.db
    return await load_canonical_map(db)
