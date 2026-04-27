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
using the hub-and-spoke model. **Returns 202 immediately** — document processing
happens asynchronously. The response contains deterministic IDs (computed from
`corpus_id` + filenames) that are valid for immediate use in BFS/search queries;
`chunk_ids`, `nodes_created`, etc. are empty in the 202 response.

Each document gets one LLM summary → SPO extraction pass (not per chunk). Raw
chunks are stored as vector-indexed `chunk` nodes (no triple extraction). Code
files (`.py`, `.js`, `.ts`, etc.) additionally get deterministic structural edges
(imports/defines/has_method) via stdlib `ast` — no LLM for code structure. All
nodes and edges are tagged `source=corpus_id`. Re-submitting with the same
`corpus_id` appends to the existing corpus.

**Processing mode** (controlled by `LETHE_SERVICE_URL` env var):
- **Fan-out** (recommended, `LETHE_SERVICE_URL` set): background task runs Phase 1 (fast corpus + document node upserts), then fans out one authenticated HTTPS call per new/changed document to `POST /v1/ingest/corpus/document` on the same service. Each call is handled by an independent Cloud Run instance, so any number of documents can be ingested in parallel with no single-request timeout risk.
- **In-process** (`LETHE_SERVICE_URL` unset): entire pipeline runs as a background task on the same instance (suitable for local dev and small corpora).

**Request** (`CorpusIngestRequest`):
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `corpus_id` | string | no | generated UUID | Re-use to append to existing corpus |
| `documents` | list[DocumentItem] | yes (min 1) | — | Each item: `{ "text": "...", "filename": "..." }` |
| `user_id` | string | no | `"global"` | User scope |
| `domain` | string | no | `"general"` | Namespace |
| `chunk_size` | int | no | `600` | Approximate words per chunk |

`filename` controls chunking strategy: `.py`/`.js`/`.ts`/etc → code (function/class splits); everything else → prose (paragraph splits).

**Response** (`CorpusIngestResponse`) — `202 Accepted`:
```json
{
  "corpus_id": "uuid-or-caller-provided",
  "corpus_node_id": "deterministic-corpus-hub-id",
  "document_ids": ["deterministic-doc-id-1", "deterministic-doc-id-2"],
  "chunk_ids": [],
  "total_chunks": 0,
  "nodes_created": [],
  "nodes_updated": [],
  "relationships_created": []
}
```

`corpus_node_id` and `document_ids` are deterministic (SHA-1 of corpus_id + filename) and immediately valid for BFS expansion and search even before background processing completes.

`chunk_ids`, `nodes_created`, etc. are always `[]`/`0` in the 202 response — they are populated in the background.

---

## POST /v1/ingest/corpus/document
Internal fan-out endpoint. Called by `POST /v1/ingest/corpus` (fan-out mode) to
process one document per Cloud Run invocation.

**Request** (`CorpusDocumentRequest`):
| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `corpus_id` | string | yes | Parent corpus |
| `corpus_node_id` | string | yes | Hub node ID |
| `doc_id` | string | yes | Pre-computed stable document node ID |
| `doc` | DocumentItem | yes | `{ "text": "...", "filename": "..." }` |
| `is_new` | bool | yes | True if document was just created in Phase 1 |
| `user_id` | string | no | `"global"` |
| `domain` | string | no | `"general"` |
| `chunk_size` | int | no | `600` |
| `ts` | string | yes | ISO-8601 timestamp from Phase 1 |
| `doc_idx` | int | yes | 0-based index for logging |
| `total_docs` | int | yes | Total document count for logging |

**Response** (`CorpusDocumentResponse`) — `201 Created`:
```json
{
  "doc_id": "doc_...",
  "chunk_ids": ["chunk-uuid-1", "..."],
  "nodes_created": ["entity_abc", "..."],
  "nodes_updated": [],
  "relationships_created": ["rel_xyz", "..."]
}
```

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
| `source_filter` | string | no | null | If set, exclude BFS candidates whose `source` != this value (nodes with `source=null` always pass) |

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
Uses same request schema as `/v1/graph/expand` (including `debug` and `source_filter`).
`source_filter` is applied on both BFS passes.

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
