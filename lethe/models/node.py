from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from lethe.constants import (
    CHUNK_SNIPPET_LENGTH,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_DOMAIN,
    DEFAULT_RELATIONSHIP_WEIGHT,
    DEFAULT_USER_ID,
    SOURCE_LOG_SNIPPET_LENGTH,
)


class Node(BaseModel):
    uuid: str
    node_type: str
    content: str
    domain: str = DEFAULT_DOMAIN
    weight: float = 0.5
    metadata: str = "{}"
    journal_entry_ids: list[str] = Field(default_factory=list)
    name_key: str | None = None
    user_id: str = DEFAULT_USER_ID
    source: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    embedding: list[float] | None = Field(default=None, exclude=True)


class Edge(BaseModel):
    uuid: str
    subject_uuid: str
    predicate: str
    object_uuid: str
    content: str = ""
    weight: float = DEFAULT_RELATIONSHIP_WEIGHT
    domain: str = DEFAULT_DOMAIN
    user_id: str = DEFAULT_USER_ID
    source: str | None = None
    journal_entry_ids: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class IngestRequest(BaseModel):
    text: str
    domain: str = DEFAULT_DOMAIN
    source: str | None = None
    user_id: str = DEFAULT_USER_ID
    timestamp: datetime | None = None


class IngestResponse(BaseModel):
    entry_uuid: str
    nodes_created: list[str] = Field(default_factory=list)
    nodes_updated: list[str] = Field(default_factory=list)
    relationships_created: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str
    node_types: list[str] = Field(default_factory=list)
    domain: str | None = None
    user_id: str = DEFAULT_USER_ID
    limit: int = 20
    min_significance: float = 0.0


class SearchResponse(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    count: int = 0

    @model_validator(mode="after")
    def _set_count(self) -> SearchResponse:
        self.count = len(self.nodes) + len(self.edges)
        return self


class GraphExpandRequest(BaseModel):
    seed_ids: list[str]
    query: str | None = Field(default=None, max_length=500)
    hops: int = 2
    limit_per_edge: int = 20
    self_seed_neighbor_floor: int = 40
    debug: bool = False
    user_id: str = DEFAULT_USER_ID
    source_filter: str | None = None


class GraphExpandResponse(BaseModel):
    nodes: dict[str, Node]
    edges: list[Edge]

    def to_markdown(
        self,
        seed_ids: list[str],
        source_logs: dict[str, list[Node]] | None = None,
    ) -> str:
        lines = ["## Knowledge Graph\n"]
        chunk_nodes: list[Node] = []
        for uuid, node in self.nodes.items():
            if node.node_type == "log":
                continue
            if node.node_type == "chunk":
                chunk_nodes.append(node)
                continue
            marker = " [SEED]" if uuid in seed_ids else ""
            lines.append(
                f"- **{node.node_type}** `{uuid[:8]}`{marker}: {node.content} "
                f"(metadata={node.metadata})"
            )
            log_nodes: list[Node] = []
            if source_logs and uuid in source_logs:
                log_nodes = source_logs[uuid]
            else:
                for log_id in reversed(node.journal_entry_ids):
                    log_node = self.nodes.get(log_id)
                    if log_node and log_node.node_type == "log":
                        log_nodes.append(log_node)
            for log_node in log_nodes:
                snippet = (log_node.content or "")[:SOURCE_LOG_SNIPPET_LENGTH]
                lines.append(f'  [source] "{snippet}"')
        lines.append("\n## Relationships\n")
        for edge in self.edges:
            subj = self.nodes.get(edge.subject_uuid)
            obj = self.nodes.get(edge.object_uuid)
            subj_label = subj.content[:40] if subj else edge.subject_uuid[:8]
            obj_label = obj.content[:40] if obj else edge.object_uuid[:8]
            lines.append(f"- {subj_label} --[{edge.predicate}]--> {obj_label}")
        if chunk_nodes:
            lines.append("\n## Source Chunks\n")
            for chunk in chunk_nodes:
                try:
                    meta = json.loads(chunk.metadata or "{}")
                except (TypeError, ValueError):
                    meta = {}
                filename = meta.get("filename", "")
                idx = meta.get("chunk_index", "")
                header = f"[{filename} chunk {idx}]" if filename else "[chunk]"
                snippet = (chunk.content or "")[:CHUNK_SNIPPET_LENGTH]
                lines.append(f'{header} "{snippet}"')
        return "\n".join(lines)


class GraphSummarizeResponse(BaseModel):
    summary: str
    debug_reasoning: dict | None = None


class DocumentItem(BaseModel):
    text: str = Field(..., min_length=1)
    filename: str


class CorpusIngestRequest(BaseModel):
    corpus_id: str | None = None
    documents: list[DocumentItem] = Field(..., min_length=1)
    user_id: str = DEFAULT_USER_ID
    domain: str = DEFAULT_DOMAIN
    chunk_size: int = DEFAULT_CHUNK_SIZE


class CorpusIngestResponse(BaseModel):
    corpus_id: str
    corpus_node_id: str = ""
    document_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    total_chunks: int = 0
    nodes_created: list[str] = Field(default_factory=list)
    nodes_updated: list[str] = Field(default_factory=list)
    relationships_created: list[str] = Field(default_factory=list)
    ingest_ts: str = ""


class CorpusStatusRequest(BaseModel):
    document_ids: list[str]
    user_id: str = DEFAULT_USER_ID
    ingest_ts: str = ""


class CorpusStatusResponse(BaseModel):
    corpus_id: str
    total: int
    completed: int
    failed: int = 0
    is_complete: bool


class CorpusDocumentRequest(BaseModel):
    """Single-document processing request used by the fan-out endpoint."""

    corpus_id: str
    corpus_node_id: str
    doc_id: str
    doc: DocumentItem
    is_new: bool
    user_id: str = DEFAULT_USER_ID
    domain: str = DEFAULT_DOMAIN
    chunk_size: int = DEFAULT_CHUNK_SIZE
    ts: str
    doc_idx: int
    total_docs: int


class CorpusDocumentResponse(BaseModel):
    doc_id: str
    chunk_ids: list[str] = Field(default_factory=list)
    nodes_created: list[str] = Field(default_factory=list)
    nodes_updated: list[str] = Field(default_factory=list)
    relationships_created: list[str] = Field(default_factory=list)
