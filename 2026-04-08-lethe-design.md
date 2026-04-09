# Lethe — Memory-as-a-Service Design Spec

**Date:** 2026-04-08  
**Status:** Approved

---

## 1. Vision & Scope

Lethe is a Memory-as-a-Service graph database tailored for LLM retrieval. It accepts raw text, extracts structured knowledge as a graph, and exposes that graph for hybrid semantic search and traversal. Each deployment is a dedicated Cloud Run service + Firestore instance scoped to one project.

Lethe is a graph database — not an application. It handles storage, indexing, and retrieval. Application-level concerns (task management, query logging, LLM reranking, pending questions) stay in the caller.

**In scope:**
- Raw text ingestion with LLM-driven SPO extraction, entity deduplication, and relationship creation
- Episodic log entry storage
- Hybrid vector + keyword + RRF search
- BFS multi-hop graph traversal
- Schema-agnostic node types registered at deploy time
- Optional LLM fact collision detection (config flag)
- `user_id` field on all nodes (defaults to `"global"`)

**Out of scope (caller's responsibility):**
- Task management
- Query logging
- LLM reranking of results
- Pending questions (revisit later)
- Application-level authentication (Cloud Run IAM handles access)

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Cloud Run Container                │
│                                                      │
│  ┌──────────────┐    ┌───────────────────────────┐  │
│  │  FastAPI App │    │     Domain Modules         │  │
│  │              │    │                            │  │
│  │  /v1/ingest  │───▶│  lethe/graph/              │  │
│  │  /v1/search  │    │    ingest.py               │  │
│  │  /v1/graph   │    │    search.py               │  │
│  │  /v1/nodes   │    │    traverse.py             │  │
│  │  /v1/entries │    │    collision.py (optional) │  │
│  │  /v1/admin   │    │    ensure_node.py          │  │
│  └──────────────┘    └─────────────┬──────────────┘  │
│                                    │                  │
│                      ┌─────────────▼──────────────┐  │
│                      │     Infrastructure          │  │
│                      │  lethe/infra/               │  │
│                      │    firestore.py             │  │
│                      │    embedder.py (Protocol)   │  │
│                      │    llm.py (Protocol)        │  │
│                      │    gemini.py (impl)         │  │
│                      └─────────────┬──────────────┘  │
└────────────────────────────────────┼─────────────────┘
                                     │
              ┌──────────────────────┼──────────────┐
              │                      │              │
         Firestore              Vertex AI        Gemini
         (nodes +             (embeddings)       (LLM)
          vectors)
```

**Layer responsibilities:**

- **FastAPI routers** — validate request/response shapes with Pydantic, call domain modules, return JSON. No business logic.
- **Domain modules** (`lethe/graph/`) — all intelligence: ingest pipeline, SPO extraction, entity deduplication, hybrid search (vector + keyword + RRF), BFS graph traversal, optional collision detection. Testable without HTTP.
- **Infrastructure** (`lethe/infra/`) — Firestore async client, `Embedder` and `LLMDispatcher` Protocols, Gemini implementation. All external I/O isolated here.
- **Config** (`lethe/config.py`) — pydantic-settings loaded from env vars.

---

## 3. Data Model

### 3.1 Node document (single `nodes` collection, name configurable)

All node types live in one Firestore collection distinguished by `node_type`.

```
{
  "uuid":               string,       // document ID
  "node_type":          string,       // registered type (e.g. "person", "generic", "log")
  "content":            string,       // human-readable text; used for embedding + keyword search
  "domain":             string,       // logical grouping (e.g. "work", "personal")
  "weight":             float,        // significance score (0.0–1.0)
  "metadata":           string,       // JSON string; arbitrary structure, caller-defined per node_type; stored as string in Firestore
  "embedding":          vector(768),  // Firestore vector field; generated by Lethe on write
  "entity_links":       [string],     // UUIDs of related nodes (entity graph edges)
  "predicate":          string|null,  // SPO: relationship verb (e.g. "works_at")
  "object_uuid":        string|null,  // SPO: UUID of object node
  "subject_uuid":       string|null,  // SPO: UUID of subject node
  "journal_entry_ids":  [string],     // UUIDs of source episodic log entries
  "name_key":           string|null,  // lowercase normalized name for entity dedup
  "hot_edges":          [string],     // bounded 20-slot most-recent relationship UUIDs
  "relevance_score":    float|null,   // used by hot_edges eviction
  "user_id":            string,       // defaults to "global"
  "source":             string|null,  // caller identifier (e.g. "jot")
  "created_at":         timestamp,
  "updated_at":         timestamp
}
```

**Episodic log entries** use `node_type: "log"`. They are raw timestamped text linked to knowledge nodes via `entity_links`.

**Relationship nodes** use `node_type: "relationship"`. Their document ID is a deterministic SHA1 of `subject_uuid:predicate:object_uuid` — re-observing the same triple appends to `journal_entry_ids` rather than creating a duplicate.

**Entity nodes** use a SHA1 stable document ID derived from `node_type:lowercase_name`, enabling fast exact-match lookups without a query.

### 3.2 Node type registry

At startup, Lethe loads valid node types and allowed predicates from Firestore `_config/canonical_map`. Ingest requests with unregistered node types are rejected with HTTP 400. The LLM may propose `NEW:predicate` to extend the predicate ontology, which Lethe appends to the canonical map automatically.

### 3.3 Firestore indexes required

- Vector index on `embedding` field
- Composite: `user_id` + `node_type` + `updated_at`
- Composite: `user_id` + `domain`
- Composite: `node_type` + `name_key` (entity dedup backfill path)

---

## 4. REST API

All endpoints under `/v1`. Pydantic models for request/response validation.

### Ingest
```
POST /v1/ingest
```
Body:
```json
{
  "text":      "Alice joined Acme Corp as VP of Engineering.",
  "domain":    "work",
  "source":    "jot",
  "user_id":   "user_abc",      // optional, defaults to "global"
  "timestamp": "2026-04-08T10:00:00Z"  // optional, defaults to now
}
```
Response:
```json
{
  "entry_uuid":            "abc123",
  "nodes_created":         ["uuid1", "uuid2"],
  "nodes_updated":         ["uuid3"],
  "relationships_created": ["rel_uuid1"]
}
```

### Search
```
POST /v1/search
```
Body: `{ query, node_types[], domain, user_id, limit, min_significance }`  
Returns: ranked list of nodes (vector + keyword + RRF fusion).

### Graph
```
POST /v1/graph/expand
```
Body: `{ seed_ids[], query, hops, limit_per_edge, user_id }`  
Returns: `{ nodes: {uuid: node}, edges: [{subject, predicate, object}] }`

### Nodes
```
GET  /v1/nodes/{uuid}
GET  /v1/nodes              # list; filter: node_type, domain, user_id, limit, offset
DELETE /v1/nodes/{uuid}
```

### Entries
```
GET  /v1/entries            # list; filter: user_id, limit, ascending, since
GET  /v1/entries/{uuid}
DELETE /v1/entries/{uuid}
```

### Admin
```
GET  /v1/health
GET  /v1/node-types         # list registered node types and allowed predicates
POST /v1/admin/backfill     # backfill missing embeddings; body: { limit }
```

---

## 5. Ingest Pipeline

`POST /v1/ingest` is the core of Lethe. Modelled on jot's refinery pipeline.

### Steps

**1. Store episodic log**  
Write a `log` node with the raw text, generate its embedding, persist to Firestore. Get back `entry_uuid`.

**2. Extract SPO triples via LLM**  
Call the LLM with a refinery-style prompt (based on jot's `refinery.txt`). The prompt lists the registered node types and allowed predicates from `_config/canonical_map`. LLM returns key/value lines — never JSON:

```
status: ok | none
triples:
Subject | Predicate | Object | SubjectType | ObjectType
```

Maximum 5 high-confidence triples. If `status: none`, pipeline ends after log storage.

**3. Resolve and commit each triple**

For each parsed triple:
- **`EnsureNode(subject)`** — resolution order:
  1. Vector search (cosine distance < 0.15) scoped to node_type — catches name variants
  2. SHA1 stable document ID fast path
  3. `name_key` exact match (backfill for pre-existing nodes)
  4. Create new node in a Firestore transaction
- **`EnsureNode(object)`** — same
- **`CreateRelationshipNode(subject, predicate, object)`** — deterministic SHA1 document ID from `subject_uuid:predicate:object_uuid`. Re-observing an existing triple appends to `journal_entry_ids` and refreshes `timestamp`; does not duplicate.
- **Add entity link backlinks:** subject→rel, object→rel, entry→rel
- **Update hot edges** on the object node (bounded 20-slot array; evict lowest `relevance_score` when full)

**4. Fact collision check (optional, config flag)**  
During `EnsureNode`, if a near-match exists (cosine distance < 0.25), the LLM decides `update` vs `insert`:
```
System: Compare New Fact to Existing Fact. Reply with ONLY 'update' or 'insert'.
```
Falls back to `insert` on LLM error. Skip if `LETHE_COLLISION_DETECTION=false`.

**5. Return manifest**  
```json
{
  "entry_uuid":            "abc123",
  "nodes_created":         ["uuid1", "uuid2"],
  "nodes_updated":         ["uuid3"],
  "relationships_created": ["rel_uuid1"]
}
```

---

## 6. Search Pipeline

`POST /v1/search` runs hybrid search with RRF fusion.

1. **Embed query** — generate query vector via `Embedder` (`RETRIEVAL_QUERY` task type)
2. **Vector search** — Firestore `FindNearest` ANN query, filtered by `user_id` and optionally `node_type`/`domain`
3. **Keyword search** — Firestore field-contains scan on `content`
4. **RRF fusion** — Reciprocal Rank Fusion (`k=60`) merges both result lists into a single ranked set
5. **Significance filter** — drop nodes below `min_significance` threshold
6. Return ranked node list

---

## 7. Graph Traversal

`POST /v1/graph/expand` runs BFS from one or more seed nodes.

- **Seed fetch** — retrieve seed nodes by UUID
- **Each hop** — fire `QueryIncomingSPOEdges` and `QueryNodesLinkingTo` concurrently; traverse `object_uuid` as intrinsic outgoing SPO edge; follow `entity_links`
- **Cycle detection** — `visited` set prevents re-expansion
- **Semantic pruning** — if `query` is provided, embed it and prune frontier to top-K by cosine similarity; otherwise apply hard cap of `limit_per_edge`
- **Batch fetch** — fetch frontier nodes in chunks of 100
- Returns `{ nodes, edges }` suitable for LLM injection as Markdown

---

## 8. Infrastructure & Deployment

### File layout
```
lethe/
  main.py                  # FastAPI app entrypoint
  config.py                # pydantic-settings config
  routers/
    ingest.py
    search.py
    graph.py
    nodes.py
    entries.py
    admin.py
  graph/
    ingest.py              # ingest pipeline
    ensure_node.py         # EnsureNode + CreateRelationshipNode
    search.py              # hybrid search + RRF
    traverse.py            # BFS graph expand
    collision.py           # LLM fact collision detection
    canonical_map.py       # canonical_map CRUD (_config collection)
  infra/
    firestore.py           # async Firestore client init
    embedder.py            # Embedder Protocol
    llm.py                 # LLMDispatcher Protocol
    gemini.py              # Gemini implementation of both
  prompts/
    refinery.txt           # SPO extraction prompt template
    collision.txt          # fact collision prompt
Dockerfile
requirements.txt
.env.example
scripts/
  setup-infra.sh           # one-time GCP bootstrap (APIs, Artifact Registry, Firestore, indexes)
  deploy.sh                # build image, push, deploy to Cloud Run
  tail.sh                  # tail Cloud Run logs
  graph-query.sh           # query graph via REST API; render DOT/PNG in terminal via Graphviz + imgcat
  lib/
    env-confirm.sh         # shared: require dev|prod, load .env, confirm before proceeding
firestore.indexes.json     # vector + composite index definitions
```

### Config (env vars)
| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | required | GCP project ID |
| `LETHE_COLLECTION` | `nodes` | Firestore collection name |
| `LETHE_EMBEDDING_MODEL` | `text-embedding-005` | Vertex AI embedding model |
| `LETHE_LLM_MODEL` | `gemini-2.5-flash` | Gemini model for extraction + collision |
| `LETHE_COLLISION_DETECTION` | `true` | Enable LLM fact collision check |
| `LETHE_RRF_K` | `60` | RRF fusion constant |
| `LETHE_SIMILARITY_THRESHOLD` | `0.25` | Cosine distance threshold for collision |
| `LETHE_ENTITY_THRESHOLD` | `0.15` | Cosine distance threshold for EnsureNode |
| `LETHE_REGION` | `us-central1` | Cloud Run / Firestore region |
| `LOG_LEVEL` | `info` | Logging level |

### Scripts
- **`setup-infra.sh <dev|prod>`** — enables GCP APIs (`run`, `firestore`, `aiplatform`, `artifactregistry`), creates Artifact Registry repo, initializes Firestore, deploys vector + composite indexes. Run once per new project deployment.
- **`deploy.sh <dev|prod>`** — builds Docker image tagged with git SHA, pushes to Artifact Registry, deploys to Cloud Run with env vars from `.env` / `.env.prod`.
- **`tail.sh <dev|prod>`** — polls Cloud Run logs with structured formatting, same pattern as jot.
- **`graph-query.sh <dev|prod> [-depth=N] [-limit=N] [-limit-per-edge=N] [-user-id=X] <query>`** — queries the graph via the REST API and renders it in the terminal. Calls `POST /v1/search` to find seed nodes, then `POST /v1/graph/expand` for traversal. Emits a DOT file and renders it as a PNG using `dot` (Graphviz) + `imgcat` when both are available. Falls back to a plain-text node/edge list when they are not. Nodes are labelled with `content` truncated to 40 chars and `node_type`; edges are labelled with the predicate.
- **`lib/env-confirm.sh`** — shared helper requiring explicit `dev|prod` arg, loading `.env` or `.env.prod`, prompting for confirmation before proceeding.

---

## 9. Key Design Principles

- **Graph database discipline** — Lethe stores and retrieves. Application logic stays in callers.
- **Pluggable providers** — `Embedder` and `LLMDispatcher` are Python Protocols. Gemini is the only wired implementation. Swap by implementing the Protocol and wiring at startup.
- **Schema-agnostic** — node types and allowed predicates are deploy-time config in Firestore `_config/canonical_map`. Lethe does not validate `metadata` contents.
- **Deterministic dedup** — relationship nodes and entity nodes use SHA1-derived document IDs. Re-observing the same fact appends, never duplicates.
- **Organic ontology** — the LLM may propose `NEW:predicate` during extraction; Lethe appends it to the canonical map automatically.
- **`user_id` everywhere** — defaults to `"global"`. All reads support `user_id` filtering. Enables multi-user graphs in a single deployment.
- **One collection** — all node types in one Firestore collection, distinguished by `node_type`. Same discipline as jot/memory.
- **LLM output as key/value** — never parse JSON from LLM responses. Use pipe-separated triples and key/value lines.
