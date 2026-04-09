from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from lethe.constants import DEFAULT_DOMAIN, DEFAULT_USER_ID


class Node(BaseModel):
    uuid: str
    node_type: str
    content: str
    domain: str = DEFAULT_DOMAIN
    weight: float = 0.5
    metadata: str = "{}"
    entity_links: list[str] = Field(default_factory=list)
    predicate: Optional[str] = None
    object_uuid: Optional[str] = None
    subject_uuid: Optional[str] = None
    journal_entry_ids: list[str] = Field(default_factory=list)
    name_key: Optional[str] = None
    hot_edges: list[str] = Field(default_factory=list)
    relevance_score: Optional[float] = None
    user_id: str = DEFAULT_USER_ID
    source: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    embedding: Optional[list[float]] = Field(default=None, exclude=True)


class IngestRequest(BaseModel):
    text: str
    domain: str = DEFAULT_DOMAIN
    source: Optional[str] = None
    user_id: str = DEFAULT_USER_ID
    timestamp: Optional[datetime] = None


class IngestResponse(BaseModel):
    entry_uuid: str
    nodes_created: list[str] = Field(default_factory=list)
    nodes_updated: list[str] = Field(default_factory=list)
    relationships_created: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str
    node_types: list[str] = Field(default_factory=list)
    domain: Optional[str] = None
    user_id: str = DEFAULT_USER_ID
    limit: int = 20
    min_significance: float = 0.0


class SearchResponse(BaseModel):
    results: list[Node]
    count: int = 0


class Edge(BaseModel):
    subject: str
    predicate: str
    object: str


class GraphExpandRequest(BaseModel):
    seed_ids: list[str]
    query: Optional[str] = None
    hops: int = 2
    limit_per_edge: int = 20
    self_seed_neighbor_floor: int = 40
    debug: bool = True
    user_id: str = DEFAULT_USER_ID


class GraphExpandResponse(BaseModel):
    nodes: dict[str, Node]
    edges: list[Edge]

    def to_markdown(self, seed_ids: list[str]) -> str:
        lines = ["## Knowledge Graph\n"]
        for uuid, node in self.nodes.items():
            marker = " [SEED]" if uuid in seed_ids else ""
            lines.append(
                f"- **{node.node_type}** `{uuid[:8]}`{marker}: {node.content} "
                f"(metadata={node.metadata})"
            )
            if node.journal_entry_ids:
                for log_id in reversed(node.journal_entry_ids):
                    log_node = self.nodes.get(log_id)
                    if log_node and log_node.node_type == "log":
                        snippet = (log_node.content or "")[:150]
                        lines.append(f"  - Source Snippet: {snippet}")
                        break
        lines.append("\n## Relationships\n")
        for edge in self.edges:
            subj = self.nodes.get(edge.subject)
            obj = self.nodes.get(edge.object)
            subj_label = subj.content[:40] if subj else edge.subject[:8]
            obj_label = obj.content[:40] if obj else edge.object[:8]
            lines.append(f"- {subj_label} --[{edge.predicate}]--> {obj_label}")
        return "\n".join(lines)


class GraphSummarizeResponse(BaseModel):
    summary: str
    debug_reasoning: Optional[dict] = None
