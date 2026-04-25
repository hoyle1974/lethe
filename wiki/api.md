# API Reference

Base URL: `http://<host>:<port>`
All endpoints: JSON over HTTP.
Auth: Cloud Run IAM only — no application-level auth. `user_id` is caller-supplied.

---

## GET /v1/health
Liveness check. No Firestore or LLM required.
**Response**: `{ "status": "ok" }`

---

## GET /v1/node-types
Returns current canonical vocabulary.
**Response**:
```json
{
  "node_types": ["person","place","event","project","goal","preference","asset","tool","generic","log"],
  "allowed_predicates": ["works_at","lives_in","knows","is_part_of","owns","uses",
                         "participates_in","located_at","created_by","manages",
                         "reports_to","related_to","is_a"]
}
```
Vocabulary is seeded from Firestore at startup and extended at runtime by new-predicate ingestion.

---

## POST /v1/ingest
Ingest free text. Stores log node + extracts SPO triples → entity nodes + edges.
Always returns 200. Extraction errors are logged and skipped, not surfaced.

**Request**:
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `text` | string | yes | — | Free-text input |
| `domain` | string | no | `"general"` | Namespace within user graph |
| `source` | string | no | null | Provenance label |
| `user_id` | string | no | `"global"` | User scope |
| `timestamp` | string | no | now | ISO-8601 override |

**Response** (`IngestResponse`):
```json
{
  "entry_uuid": "3fa85f64-...",
  "nodes_created": ["entity_a1b2"],
  "nodes_updated": [],
  "relationships_created": ["rel_abc123"]
}
```

---

## POST /v1/ingest/corpus
Ingest a collection of related documents (e.g. a codebase) as a single corpus
using the hub-and-spoke model. Each document gets one LLM summary → SPO extraction
pass (not per chunk). Raw chunks are stored as vector-indexed `chunk` nodes (no triple
extraction). Code files (`.py`, `.js`, `.ts`, etc.) additionally get deterministic
structural edges (imports/defines/has_method) via stdlib `ast` — no LLM for code
structure. All nodes and edges are tagged `source=corpus_id`. Re-submitting with the
same `corpus_id` appends to the existing corpus.

**Request** (`CorpusIngestRequest`):
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `corpus_id` | string | no | generated UUID | Re-use to append to existing corpus |
| `documents` | list[DocumentItem] | yes (min 1) | — | Each item: `{ "text": "...", "filename": "..." }` |
| `user_id` | string | no | `"global"` | User scope |
| `domain` | string | no | `"general"` | Namespace |
| `chunk_size` | int | no | `600` | Approximate words per chunk |

`filename` controls chunking strategy: `.py`/`.js`/`.ts`/etc → code (function/class splits); everything else → prose (paragraph splits).

**Response** (`CorpusIngestResponse`):
```json
{
  "corpus_id": "uuid-or-caller-provided",
  "document_ids": ["doc-uuid-1", "doc-uuid-2"],
  "chunk_ids": ["chunk-uuid-1", "chunk-uuid-2", "..."],
  "total_chunks": 42,
  "nodes_created": ["entity_abc", "..."],
  "nodes_updated": [],
  "relationships_created": ["rel_xyz", "..."]
}
```

`chunk_ids` — UUIDs of `node_type="chunk"` nodes. Use for targeted vector search against raw source text.

---

## POST /v1/search
Hybrid vector search. Embeds query, searches nodes + edges, applies temporal decay ranking.

**Request** (`SearchRequest`):
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `query` | string | yes | — | Natural-language query |
| `node_types` | list[str] | no | all non-log | Filter by type |
| `domain` | string | no | null | Domain filter |
| `user_id` | string | no | `"global"` | User scope |
| `limit` | int | no | 20 | Max results |
| `min_significance` | float | no | 0.0 | Min node/edge weight |

**Response** (`SearchResponse`):
```json
{ "nodes": [...], "edges": [...], "count": N }
```

---

## POST /v1/graph/expand
BFS expansion from seed UUIDs. Returns subgraph of connected nodes + edges.
Tombstoned nodes (weight 0.0) are excluded.

**Request** (`GraphExpandRequest`):
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `seed_ids` | list[str] | yes | — | Starting node UUIDs |
| `query` | string | no | null | Used for frontier pruning scoring (max 500 chars) |
| `hops` | int | no | 2 | BFS depth |
| `limit_per_edge` | int | no | 20 | Max nodes per hop |
| `self_seed_neighbor_floor` | int | no | 40 | Min SELF-node neighbors guaranteed at hop 1 |
| `user_id` | string | no | `"global"` | User scope |
| `debug` | bool | no | false | Include debug_reasoning in response |

**Response** (`GraphExpandResponse`):
```json
{
  "nodes": { "<uuid>": { ...Node }, ... },
  "edges": [ ...Edge ]
}
```

---

## POST /v1/graph/summarize
LLM-grounded summary of a graph neighbourhood. Two-pass retrieval loop.
Uses same request schema as `/v1/graph/expand` (including `debug`).

**Response** (`GraphSummarizeResponse`):
```json
{
  "summary": "## Profile\n\nAlice is...",
  "debug_reasoning": null
}
```
Query mode auto-detected: ≤2 words → broad profile; `?` or question words → Q&A; else → free-form.
If summary < 100 chars, system auto-retries once.

---

## POST /v1/admin/consolidate
Distils recent log nodes into up to 3 durable factual statements, re-ingested as `core_memory` domain.

**Request**: `{ "user_id": "alice" }`
**Response**: `{ "statements": ["..."], "ingest_results": [...] }`

---

## POST /v1/admin/backfill
Generate embeddings for nodes missing them.

**Request**: `{ "limit": 100 }`
**Response**: `{ "backfilled": 42 }`

---

## GET /v1/nodes/{uuid}
Fetch a single node by UUID.
**Response**: Node object or 404.

---

## GET /v1/nodes
List nodes. Supports query params for filtering.
**Response**: `list[Node]`

---

## GET /v1/entries/{uuid}
Fetch a single log entry (episodic log node) by UUID.
**Response**: Node object or 404.

---

## GET /v1/entries
List log entries. Supports query params for filtering.
**Response**: `list[Node]`
