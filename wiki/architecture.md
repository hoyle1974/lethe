# Architecture

## Identity
- **Name**: Lethe
- **Role**: Memory-as-a-Service — graph database for LLM retrieval
- **Not**: an application. No task management, no query logging, no reranking. All caller concerns.

## Tech Stack
- Python 3.14
- FastAPI + uvicorn (ASGI server); uvloop event loop
- google-cloud-firestore (async) — sole persistence backend
- google-cloud-aiplatform (Vertex AI) — LLM + embeddings via Gemini
- pydantic-settings — config from `.env` / environment variables
- Jinja2 — prompt templating

## Deployment
- Cloud Run (Docker container)
- `Dockerfile` at repo root
- All config via environment variables / Cloud Run secrets
- IAM-only auth (no application-level auth)

## Key Config (`lethe/config.py` — `Config` class via pydantic-settings)
| Env var | Default | Purpose |
|---------|---------|---------|
| `GOOGLE_CLOUD_PROJECT` | required | GCP project |
| `LETHE_COLLECTION` | `nodes` | Firestore nodes collection |
| `LETHE_RELATIONSHIPS_COLLECTION` | `relationships` | Firestore edges collection |
| `LETHE_EMBEDDING_MODEL` | `text-embedding-005` | Vertex AI embedding model |
| `LETHE_LLM_MODEL` | `gemini-2.5-flash` | Gemini model |
| `LETHE_COLLISION_DETECTION` | `true` | Enable LLM fact-collision check |
| `LETHE_SIMILARITY_THRESHOLD` | `0.25` | Semantic search cosine threshold |
| `LETHE_ENTITY_THRESHOLD` | `0.15` | Entity dedup cosine threshold |
| `LETHE_REGION` | `us-central1` | Vertex AI region |
| `LETHE_SERVICE_URL` | `""` | Cloud Run service URL — enables fan-out corpus processing |

## Startup Sequence (`lethe/main.py` lifespan)
1. Instantiate `Config` (reads env)
2. Create async Firestore client → `app.state.db`
3. Instantiate `GeminiEmbedder` → `app.state.embedder`
4. Instantiate `GeminiLLM` → `app.state.llm`
5. `seed_canonical_map(db)` — write default node types + predicates to Firestore if absent
6. `load_canonical_map(db)` → `app.state.canonical_map` (in-memory vocabulary)
7. Configure logging

## Routers
| Module | Responsibilities |
|--------|-----------------|
| `lethe/routers/admin.py` | GET /v1/health, GET /v1/node-types, POST /v1/admin/consolidate, POST /v1/admin/backfill |
| `lethe/routers/ingest.py` | POST /v1/ingest, POST /v1/ingest/corpus (202), POST /v1/ingest/corpus/document |
| `lethe/routers/search.py` | POST /v1/search |
| `lethe/routers/graph.py` | POST /v1/graph/expand, POST /v1/graph/summarize |
| `lethe/routers/nodes.py` | GET /v1/nodes/{uuid}, GET /v1/nodes |
| `lethe/routers/entries.py` | GET /v1/entries/{uuid}, GET /v1/entries |

## Data Flow (Ingest)
```
POST /v1/ingest
  → router calls run_ingest() [lethe/graph/ingest.py]
    → store log node in Firestore (nodes collection)
    → extract_triples() via Gemini [lethe/graph/extraction.py]
    → for each triple: resolve terms → ensure_node() → create_relationship_node()
  → return IngestResponse
```

## Data Flow (Corpus Ingest)
```
POST /v1/ingest/corpus  →  202 Accepted immediately (deterministic IDs pre-computed)
  ↓ background task
  [fan-out mode: LETHE_SERVICE_URL set]
    → run_corpus_setup(): Phase 1 — upsert corpus node + classify docs (content_hash check)
    → fanout_corpus_documents(): one HTTPS call per new/changed doc to self
        → Cloud Run auto-scales; each call → POST /v1/ingest/corpus/document

  [in-process mode: LETHE_SERVICE_URL unset]
    → run_corpus_ingest(): Phase 1 + Phase 2 on same instance (max 3 concurrent LLM)

POST /v1/ingest/corpus/document  →  201 (fan-out target, one Cloud Run invocation per doc)
    → tombstone old chunks (if changed)
    → summarize_document() via Gemini
    → run_ingest(summary) → entity + relationship nodes
    → chunk_document() → store chunk nodes (no LLM)
    → _ingest_structural_edges() (code files only, no LLM)
```

## Data Flow (Search)
```
POST /v1/search
  → embed query (Vertex AI)
  → Firestore vector search on nodes collection
  → Firestore vector search on relationships collection
  → merge + temporal-decay score + filter
  → return SearchResponse
```

## Data Flow (Graph Expand)
```
POST /v1/graph/expand
  → embed query if provided
  → BFS from seed UUIDs [lethe/graph/traverse.py]
    → per hop: fetch edges → collect neighbor IDs → fetch nodes → prune by similarity+observation
  → return GraphExpandResponse
```

## Infra Layer (`lethe/infra/`)
- `firestore.py` — create_firestore_client()
- `gemini.py` — GeminiEmbedder, GeminiLLM (implement Embedder/LLMDispatcher protocols)
- `llm.py` — LLMDispatcher protocol + LLMRequest dataclass
- `embedder.py` — Embedder protocol
- `fs_helpers.py` — Firestore helpers: FieldFilter, Vector, ArrayUnion
