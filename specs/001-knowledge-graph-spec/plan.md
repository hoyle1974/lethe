# Implementation Plan: Lethe Knowledge Graph API

**Branch**: `001-knowledge-graph-spec` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/001-knowledge-graph-spec/spec.md`

## Summary

Lethe is an async REST API (FastAPI + uvicorn/uvloop) that serves as a personal knowledge graph
engine. It accepts free-text input, uses a Gemini LLM to extract structured knowledge as
subject–predicate–object triples, persists those triples as typed nodes and edges in Firestore,
and provides semantic vector search and graph traversal operations. All infrastructure dependencies
(Firestore client, LLM dispatcher, text embedder) are injected at startup via FastAPI's lifespan
context and accessed through a dependency-injection layer (`deps.py`).

## Technical Context

**Language/Version**: Python 3.14
**Primary Dependencies**: FastAPI, uvicorn, uvloop, google-cloud-firestore, google-cloud-aiplatform
(Vertex AI), pydantic, pydantic-settings, jinja2, ruff
**Storage**: Google Cloud Firestore (two collections: `nodes` and `relationships`; one config
collection `_config`)
**Testing**: pytest + pytest-asyncio (`asyncio_mode = auto`); GCP stubs at `sys.modules` level in
`conftest.py`; no live GCP calls in tests
**Target Platform**: Linux server (GCP-hosted, though runnable locally with a `.env` file)
**Project Type**: Web service (HTTP REST API)
**Performance Goals**: Not formally specified; async I/O throughout; Firestore vector ANN search
bounded by `_SEARCH_POOL_MAX = 200` candidates
**Constraints**: All graph operations scoped to `user_id`; `lethe_entity_threshold = 0.15`
(cosine) for entity deduplication; `lethe_similarity_threshold = 0.25` for general search
**Scale/Scope**: Single-user to small-team personal knowledge assistant; no multi-tenancy
enforcement beyond `user_id` filtering

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Async-First | ✅ Pass | All graph ops, infra calls, and route handlers are `async def`. No blocking calls in async context. |
| II. Dependency Injection via app.state | ✅ Pass | `db`, `llm`, `embedder` wired in `lifespan`; accessed via `deps.py` functions. Business logic receives them as parameters; no singleton imports. |
| III. GCP Test Isolation | ✅ Pass | `conftest.py` stubs `vertexai`, `vertexai.language_models`, `vertexai.generative_models` at `sys.modules` level before any lethe import. `MockEmbedder` and `MockLLM` fixtures provided. `asyncio_mode = auto`. |
| IV. Graph Semantics | ✅ Pass | All domain knowledge stored as `Node` (entity/log) and `Edge` (SPO triple). `predicate` is always a normalised string. No free-form relationship storage. |
| V. Style Discipline | ✅ Pass | `ruff` configured in `pyproject.toml` (line-length 100, rules E/F/I). venv-only execution documented in `CLAUDE.md`. |

**Constitution Check result: PASS — no violations.**

## Project Structure

### Documentation (this feature)

```text
specs/001-knowledge-graph-spec/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── api.md
└── tasks.md             # Phase 2 output (/speckit-tasks command)
```

### Source Code (repository root)

```text
lethe/
├── main.py              # FastAPI app factory + lifespan (infra wiring)
├── deps.py              # DI accessors: get_db, get_llm, get_embedder, get_config, get_canonical_map
├── config.py            # Pydantic Settings (env vars + .env file)
├── constants.py         # All numeric constants, string identifiers, defaults
├── types.py             # Shared type aliases (EmbeddingTaskType, etc.)
│
├── models/
│   └── node.py          # Pydantic domain models: Node, Edge, request/response schemas
│
├── graph/
│   ├── canonical_map.py # CanonicalMap dataclass + Firestore seed/load/append
│   ├── ingest.py        # run_ingest orchestrator: log → extract → resolve → upsert
│   ├── ensure_node.py   # Entity node lifecycle: create/upsert/deduplicate via hash+ANN+name_key
│   ├── extraction.py    # LLM triple extraction: prompt build, parse_refinery_output
│   ├── collision.py     # Fact-collision LLM call: "update" vs "insert"
│   ├── contradiction.py # Relationship supersession + tombstoning
│   ├── search.py        # vector_search, _edge_vector_search, execute_search + decay scoring
│   ├── traverse.py      # graph_expand BFS, frontier pruning, SELF neighbor floor
│   └── consolidate.py   # run_consolidation: log distillation → re-ingest as core_memory
│
├── infra/
│   ├── embedder.py      # Embedder protocol (runtime_checkable)
│   ├── llm.py           # LLMDispatcher protocol + LLMRequest dataclass
│   ├── gemini.py        # GeminiEmbedder + GeminiLLM concrete implementations
│   ├── firestore.py     # create_firestore_client factory
│   └── fs_helpers.py    # Firestore shims: Vector, FieldFilter, DistanceMeasure, ArrayUnion
│
├── routers/
│   ├── ingest.py        # POST /v1/ingest
│   ├── search.py        # POST /v1/search
│   ├── graph.py         # POST /v1/graph/expand, POST /v1/graph/summarize
│   ├── nodes.py         # Node management routes (read/delete)
│   ├── entries.py       # Log entry routes
│   └── admin.py         # GET /v1/health, GET /v1/node-types, POST /v1/admin/backfill, POST /v1/admin/consolidate
│
└── prompts/
    ├── refinery.txt     # Jinja2 template: triple extraction prompt
    └── collision.txt    # Fact-collision decision prompt

tests/
├── conftest.py          # GCP stubs (sys.modules) + MockEmbedder, MockLLM fixtures
└── test_*.py            # Unit tests per module (15 test files)
```

**Structure Decision**: Single Python package (`lethe/`) at repository root with a parallel `tests/`
directory. No separate frontend or CLI layer. The web service is the sole entry point.

## Phase 0: Research

### Decision Log

**D-001: Firestore as sole persistence backend**
- Decision: Google Cloud Firestore (native async client `google-cloud-firestore`)
- Rationale: Firestore provides built-in vector ANN search (`find_nearest`) without a separate
  vector database, native scalability, and serverless operation. The `google-cloud-firestore`
  async client integrates cleanly with asyncio.
- Alternatives: PostgreSQL + pgvector (requires separate infra), Pinecone (adds dependency),
  in-memory (not durable)

**D-002: Two-collection data model**
- Decision: `nodes` collection for entity/log nodes; `relationships` collection for edges.
- Rationale: Separating edges into their own collection allows independent vector indexing of
  relationship text (`"subject_content predicate object_content"`), enables edge-level search,
  and avoids Firestore subcollection complexity. Both collections are searched in parallel during
  `execute_search`.
- Alternatives: Single collection with node_type discriminator (loses edge vector search),
  subcollections per node (complicates cross-entity queries)

**D-003: Content-hash document IDs for deduplication**
- Decision: Entity nodes use `"entity_" + SHA1(node_type + ":" + lowercase_name)` as Firestore
  document ID. Relationship nodes use `"rel_" + SHA1(subject_id + ":" + predicate + ":" + object_id)`.
  Self-node uses `"entity_" + SHA1("self:" + user_id)`.
- Rationale: Deterministic IDs allow transactional create-or-update without a preliminary read
  in most cases, preventing duplicate entity creation under concurrent ingestion.
- Alternatives: Random UUIDs (require separate uniqueness index), name_key index lookup only
  (requires extra read before every write)

**D-004: Three-path entity resolution in ensure_node**
- Decision: Resolution order is (1) vector ANN within entity_threshold, (2) SHA1 stable doc ID,
  (3) name_key exact-match query, (4) transactional create.
- Rationale: Vector similarity catches aliases and spelling variants; SHA1 is a fast deterministic
  path; name_key is a safety net for cases where the SHA1 doc was written by a different code path.
  All four paths fall back to transactional create to prevent lost writes.
- Alternatives: Single name_key lookup (misses aliases), ANN only (expensive for every ingest)

**D-005: Gemini models via Vertex AI**
- Decision: `gemini-2.5-flash` for LLM (extraction, collision, supersession, consolidation,
  summarisation); `text-embedding-005` for text embeddings (768-dimensional).
- Rationale: Gemini 2.5 Flash offers high throughput with a large context window (needed for
  extraction prompt with node types + predicates + text). `text-embedding-005` is the current
  Vertex AI recommendation for retrieval tasks.
- Alternatives: OpenAI GPT-4o (no GCP integration), local models (no hosted inference)

**D-006: Temporal decay + reinforcement scoring for search**
- Decision: Search results ranked by `effective_distance = cosine_distance / (decay × reinforcement)`
  where `decay = 0.5^(age_days / half_life)` and `reinforcement = 1 + 0.05 × min(observations, 50)`.
  Half-lives: log=30d, entity=365d, edge=90d.
- Rationale: Recent knowledge is more relevant; frequently referenced nodes/edges are more
  significant. Decay keeps ephemeral logs from dominating long-lived entity results.
- Alternatives: Pure cosine ranking (ignores recency), BM25 (no semantic similarity)

**D-007: Firestore transactions for concurrent-safe node creation**
- Decision: `ensure_node` uses `@firestore.async_transactional` on the final create step.
  `create_relationship_node` similarly uses a transaction for the relationship upsert.
- Rationale: Concurrent ingestion of the same entity from two simultaneous requests would produce
  duplicate nodes without transactions. Async Firestore transactions do not support queries inside
  them, so vector search and name_key checks happen outside the transaction.
- Known limitation: The ANN query + transaction window is not atomic; a very narrow race can still
  produce two nodes for the same entity. The name_key query before the transaction closes most of
  that window.

**D-008: Jinja2-templated LLM prompts**
- Decision: Extraction prompt is a Jinja2 template (`prompts/refinery.txt`) rendered at runtime
  with the current canonical node types and predicates.
- Rationale: Allows the canonical vocabulary to influence every extraction call without hardcoding
  prompts. Template is loaded once and cached.
- Alternatives: f-strings (no separation of prompt text from code), static files (can't inject
  dynamic vocabulary)

## Phase 1: Design

### Data Model

See [`data-model.md`](./data-model.md) for the full entity definitions.

Key design decisions:
- `Node.embedding` is excluded from Pydantic serialisation (`Field(exclude=True)`) so vectors
  are never returned in API responses.
- `Node.weight` defaults to 0.55 (entity), 0.3 (log); `Edge.weight` defaults to 0.8.
  Tombstoned items have `weight = 0.0`.
- `Node.journal_entry_ids` is an append-only list of log entry UUIDs that referenced this node,
  used for reinforcement scoring.
- `Node.metadata` is stored as a JSON string (not a parsed dict) to avoid Firestore map nesting
  limits and keep the schema flexible.
- `Edge` carries a denormalised `content` field (`"subject_content predicate object_content"`)
  for embedding and display without a join.

### Ingest Flow (end-to-end)

```
POST /v1/ingest
  → run_ingest()
      1. embed(text) → store log node with entry_uuid
      2. extract_triples(llm, text, node_types, predicates)
         → build_refinery_prompt() [Jinja2 template]
         → llm.dispatch() → parse_refinery_output()
         → [status, RefineryTriple[]]
      3. For each triple:
         a. resolve_term(subject), resolve_term(object)
            - "SELF" → stable_self_id(user_id)
            - placeholder terms → drop triple
            - generated IDs → look up in Firestore, drop if not found
         b. ensure_node(subject) + ensure_node(object)
            - ANN search → collision detection → upsert or create
         c. create_relationship_node(subj, pred, obj)
            - embed(content)
            - query existing edges for supersession
            - evaluate_relationship_supersedes() → tombstone if superseded
            - transactional upsert
  → IngestResponse
```

### Search Flow

```
POST /v1/search
  → execute_search()
      1. embed(query, RETRIEVAL_QUERY)
      2. parallel: vector_search(nodes) + _edge_vector_search(edges)
      3. apply effective_distance_decay() to nodes and edges
      4. sort ascending by effective distance
      5. filter weight > 0.0, apply min_significance, slice to limit
  → SearchResponse
```

### Graph Expand Flow

```
POST /v1/graph/expand
  → graph_expand()
      1. embed(query) if provided
      2. fetch seed nodes from `nodes` collection
      3. BFS loop (hops iterations):
         a. for each frontier node: _get_edge_neighbors() from `relationships`
         b. collect candidate next-hop node IDs from edges
         c. fetch candidates in batches of 100
         d. prune_frontier_by_similarity() → top limit_per_edge by (similarity × 0.7 + observation_rate × 0.3)
         e. apply_self_seed_neighbor_floor() for hop 0 if SELF in frontier
         f. exclude tombstoned (weight 0.0) nodes from returned set; include in visited
         g. log nodes included in all_nodes but not frontier
  → GraphExpandResponse
```

### Summarise Flow

```
POST /v1/graph/summarize
  → summarize()
      1. classify query: broad (≤2 words) | question (starts with who/what/? etc.) | free-form
      2. pass 1: graph_expand(seeds) → to_markdown() → parallel: draft_summary + thought_queries
      3. thought_queries → execute_search() × 3 → retrieval_seed_ids
      4. pass 2 (if retrieval_seed_ids): graph_expand(retrieval_seed_ids, hops=1) → merge_graphs()
      5. final_summary = llm.dispatch(merged_graph_markdown)
      6. if len(final_summary) < 100: retry with explicit re-prompt
      7. if debug=True: include debug_reasoning dict
  → GraphSummarizeResponse
```

### Consolidation Flow

```
POST /v1/admin/consolidate
  → run_consolidation()
      1. query 50 most recent log nodes for user_id
      2. llm.dispatch(combined_logs) → parse up to 3 factual statements
      3. for each statement: run_ingest(domain="core_memory")
  → ConsolidationResponse
```

### Canonical Map

The `_config/canonical_map` Firestore document holds:
- `node_types`: list of allowed node type strings (seeded with 10 defaults at startup)
- `allowed_predicates`: list of normalised predicate strings (seeded with 13 defaults at startup)

The map is loaded on startup (`seed_canonical_map`) and per-request via `get_canonical_map`.
New predicates from LLM extractions are appended via `append_predicate` (Firestore `ArrayUnion`).

### Configuration

All configuration is via environment variables (or `.env` file), managed by `pydantic-settings`:

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_CLOUD_PROJECT` | required | GCP project for Firestore + Vertex AI |
| `LETHE_COLLECTION` | `"nodes"` | Firestore collection for entity/log nodes |
| `LETHE_RELATIONSHIPS_COLLECTION` | `"relationships"` | Firestore collection for edges |
| `LETHE_EMBEDDING_MODEL` | `"text-embedding-005"` | Vertex AI embedding model |
| `LETHE_LLM_MODEL` | `"gemini-2.5-flash"` | Gemini model for all LLM calls |
| `LETHE_COLLISION_DETECTION` | `true` | Enable/disable LLM fact-collision check |
| `LETHE_SIMILARITY_THRESHOLD` | `0.25` | Cosine threshold for general search |
| `LETHE_ENTITY_THRESHOLD` | `0.15` | Cosine threshold for entity deduplication ANN |
| `LETHE_REGION` | `"us-central1"` | Vertex AI region |
| `LOG_LEVEL` | `"info"` | Python logging level |

## Complexity Tracking

No constitution violations found. Complexity table not required.
