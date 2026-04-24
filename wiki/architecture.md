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

## Startup Sequence (`lethe/main.py` lifespan)
1. Instantiate `Config` (reads env)
2. Create async Firestore client → `app.state.db`
3. Instantiate `GeminiEmbedder` → `app.state.embedder`
4. Instantiate `GeminiLLM` → `app.state.llm`
5. `seed_canonical_map(db)` — write default node types + predicates to Firestore if absent
6. `load_canonical_map(db)` → `app.state.canonical_map` (in-memory vocabulary)
7. Configure logging

## Routers
| Module | Prefix | Responsibilities |
|--------|--------|-----------------|
| `lethe/routers/ingest.py` | `/v1` | POST /ingest |
| `lethe/routers/search.py` | `/v1` | POST /search |
| `lethe/routers/graph.py` | `/v1/graph` | POST /expand, POST /summarize |
| `lethe/routers/admin.py` | `/v1/admin` | POST /consolidate, POST /backfill |
| `lethe/routers/nodes.py` | `/v1` | GET /node-types |
| `lethe/routers/entries.py` | `/v1` | GET /health |

## Data Flow (Ingest)
```
POST /v1/ingest
  → router calls run_ingest() [lethe/graph/ingest.py]
    → store log node in Firestore (nodes collection)
    → extract_triples() via Gemini [lethe/graph/extraction.py]
    → for each triple: resolve terms → ensure_node() → create_relationship_node()
  → return IngestResponse
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
