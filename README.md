# Lethe

**Memory-as-a-Service** — a graph database for LLM retrieval.

Lethe accepts raw text, extracts structured knowledge as a graph (via LLM-driven triple extraction), and exposes that graph for hybrid semantic search and multi-hop traversal. Each deployment is a dedicated Cloud Run service backed by Firestore.

Lethe is a graph database, not an application. It handles storage, indexing, and retrieval. Application-level concerns (task management, query logging, LLM reranking) stay in the caller.

---

## Capabilities

- **Ingest** — post free text; Lethe extracts entities and relationships automatically
- **Search** — hybrid vector + keyword search with temporal decay ranking
- **Traverse** — BFS multi-hop graph expansion from seed node UUIDs
- **Summarize** — LLM-grounded natural-language summary of a graph neighbourhood
- **Consolidate** — distil accumulated log entries into durable factual statements
- **Schema-agnostic** — node types and predicates registered at deploy time

---

## Quickstart

### Prerequisites

- Python 3.14
- Google Cloud SDK authenticated to a project with Firestore and Vertex AI enabled

### Install

```bash
python3.14 -m venv .venv
source .venv/bin/activate
.venv/bin/pip install -e ".[dev]"
```

### Configure

Copy `.env.example` to `.env` and fill in your values:

```env
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
LETHE_COLLECTION=nodes
LETHE_RELATIONSHIPS_COLLECTION=relationships
LETHE_EMBEDDING_MODEL=text-embedding-005
LETHE_LLM_MODEL=gemini-2.5-flash
LETHE_COLLISION_DETECTION=true
LETHE_SIMILARITY_THRESHOLD=0.25
LETHE_ENTITY_THRESHOLD=0.15
LETHE_REGION=us-central1
LOG_LEVEL=info
```

Authenticate with GCP:

```bash
gcloud auth application-default login
```

### Run

```bash
.venv/bin/uvicorn lethe.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
curl http://localhost:8000/v1/health
# → {"status":"ok"}
```

---

## API Reference

All endpoints are JSON over HTTP. Base URL: `http://<host>:<port>`

### `GET /v1/health`

Server liveness check.

```json
{ "status": "ok" }
```

---

### `GET /v1/node-types`

Returns the current node type and predicate vocabulary.

```json
{
  "node_types": ["person", "place", "event", "project", "goal", "preference", "asset", "tool", "generic", "log"],
  "allowed_predicates": ["works_at", "lives_in", "knows", "is_part_of", "owns", "uses",
                         "participates_in", "located_at", "created_by", "manages",
                         "reports_to", "related_to", "is_a"]
}
```

---

### `POST /v1/ingest`

Ingest free text into the knowledge graph.

```bash
curl -X POST http://localhost:8000/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Alice works at Acme Corp as a senior engineer and lives in Seattle.",
    "user_id": "alice"
  }'
```

| Field | Required | Notes |
|-------|----------|-------|
| `text` | yes | Free-text input |
| `domain` | no | Default: `"general"` |
| `source` | no | Provenance label |
| `user_id` | no | Default: `"global"` |
| `timestamp` | no | ISO-8601 override |

```json
{
  "entry_uuid": "3fa85f64-...",
  "nodes_created": ["entity_a1b2c3", "entity_d4e5f6"],
  "nodes_updated": [],
  "relationships_created": ["rel_abc123"]
}
```

Always returns 200. Triple extraction errors are logged and skipped, not surfaced.

---

### `POST /v1/search`

Semantic search over nodes and edges with temporal decay ranking.

```bash
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Where does Alice work?",
    "user_id": "alice",
    "limit": 10
  }'
```

| Field | Required | Notes |
|-------|----------|-------|
| `query` | yes | Natural-language query |
| `node_types` | no | Filter by type; default: all non-log types |
| `domain` | no | Domain filter |
| `user_id` | no | Default: `"global"` |
| `limit` | no | Default: `20` |
| `min_significance` | no | Minimum node/edge weight; default: `0.0` |

Returns `{ "nodes": [...], "edges": [...], "count": N }`.

---

### `POST /v1/graph/expand`

BFS expansion from seed node UUIDs.

```bash
curl -X POST http://localhost:8000/v1/graph/expand \
  -H "Content-Type: application/json" \
  -d '{
    "seed_ids": ["entity_a1b2c3"],
    "query": "Alice professional context",
    "hops": 2,
    "limit_per_edge": 20,
    "user_id": "alice"
  }'
```

Returns `{ "nodes": { "<uuid>": {...}, ... }, "edges": [...] }`. Tombstoned nodes and edges (weight `0.0`) are excluded.

---

### `POST /v1/graph/summarize`

LLM-grounded natural-language summary of a graph neighbourhood. Uses the same request schema as `/v1/graph/expand`, plus an optional `debug` flag.

```bash
curl -X POST http://localhost:8000/v1/graph/summarize \
  -H "Content-Type: application/json" \
  -d '{
    "seed_ids": ["entity_a1b2c3"],
    "query": "Alice",
    "hops": 2,
    "limit_per_edge": 20,
    "user_id": "alice",
    "debug": false
  }'
```

```json
{
  "summary": "## Profile\n\nAlice is a software engineer at Acme Corp...",
  "debug_reasoning": null
}
```

Query mode is auto-detected: ≤2 words → broad profile; question words or `?` → Q&A; else → free-form.

---

### `POST /v1/admin/consolidate`

Distil recent log entries into durable factual statements and re-ingest them.

```bash
curl -X POST http://localhost:8000/v1/admin/consolidate \
  -H "Content-Type: application/json" \
  -d '{ "user_id": "alice" }'
```

```json
{
  "statements": ["Alice works at Acme Corp as a senior engineer.", "..."],
  "ingest_results": [...]
}
```

---

### `POST /v1/admin/backfill`

Backfill vector embeddings for nodes that lack them.

```bash
curl -X POST http://localhost:8000/v1/admin/backfill \
  -H "Content-Type: application/json" \
  -d '{ "limit": 100 }'
```

```json
{ "backfilled": 42 }
```

---

## Authentication

Lethe relies on Cloud Run IAM for access control. No application-level auth is implemented — callers must have a valid identity token for the Cloud Run service URL.

---

## Deployment

A `Dockerfile` is included for Cloud Run deployment. Set all environment variables as Cloud Run secrets or environment config.

---

## Further Reading

- [`specs/001-knowledge-graph-spec/spec.md`](specs/001-knowledge-graph-spec/spec.md) — full feature specification
- [`specs/001-knowledge-graph-spec/contracts/api.md`](specs/001-knowledge-graph-spec/contracts/api.md) — complete API contracts with all field schemas
- [`specs/001-knowledge-graph-spec/data-model.md`](specs/001-knowledge-graph-spec/data-model.md) — Firestore data model
- [`specs/001-knowledge-graph-spec/quickstart.md`](specs/001-knowledge-graph-spec/quickstart.md) — dev setup, testing, and troubleshooting

---

## License

[MIT](LICENSE)
