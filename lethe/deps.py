from fastapi import Request
from lethe.config import Config


def get_db(request: Request):
    return request.app.state.db


def get_embedder(request: Request):
    return request.app.state.embedder


def get_llm(request: Request):
    return request.app.state.llm


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_canonical_map(request: Request):
    return request.app.state.canonical_map
