# Data Model

## Firestore Collections

Two collections: `nodes` (configured via `LETHE_COLLECTION`) and `relationships` (via `LETHE_RELATIONSHIPS_COLLECTION`).

---

## Node (`nodes` collection)

Pydantic model: `lethe/models/node.py::Node`

| Field | Type | Notes |
|-------|------|-------|
| `uuid` | str | Document ID in Firestore |
| `node_type` | str | `"log"`, `"person"`, `"place"`, `"event"`, `"project"`, `"goal"`, `"preference"`, `"asset"`, `"tool"`, `"generic"` |
| `content` | str | Human-readable text representation |
| `domain` | str | Namespace, default `"general"`; `"core_memory"` for consolidated facts |
| `weight` | float | Relevance/decay weight (see defaults below) |
| `metadata` | str | JSON string, default `"{}"` |
| `journal_entry_ids` | list[str] | UUIDs of log nodes that reference this entity |
| `name_key` | str\|None | Stable lowercase key used for deduplication |
| `user_id` | str | Owner, default `"global"` |
| `source` | str\|None | Provenance label |
| `created_at` | datetime\|None | |
| `updated_at` | datetime\|None | |
| `embedding` | list[float]\|None | Excluded from serialization; stored separately in Firestore as Vector type |

### Node Type Variants

**Log node** (`node_type="log"`):
- Stores raw episodic text verbatim
- `weight`: `DEFAULT_LOG_WEIGHT = 0.3`
- Half-life: `LOG_NODE_HALF_LIFE_DAYS = 30.0`
- Created on every `POST /v1/ingest`

**Entity node** (typed: person, place, project, etc.):
- Deduplicated by `(node_type, name)` hash → stable document ID via `stable_entity_doc_id()`
- `weight`: `DEFAULT_ENTITY_WEIGHT = 0.55`
- Half-life: `STRUCTURED_NODE_HALF_LIFE_DAYS = 365.0`
- `journal_entry_ids` grows with each ingestion referencing the entity

**SELF node** (`node_type="person"`, special):
- Created when `"I"` / `"me"` / `"SELF"` appears in a triple
- Stable UUID via `stable_self_id(user_id)` — deterministic, per-user
- Content: `"Me"`

**Corpus node** (`node_type="corpus"`):
- Hub for a multi-document corpus ingestion
- Stable UUID via `stable_corpus_node_id(corpus_id)` — deterministic SHA-1
- `weight`: `CORPUS_NODE_WEIGHT = 1.0`
- Content: `"Corpus '{corpus_id}': file1.py, file2.md, ..."` — searchable by name
- `source = corpus_id`

**Document node** (`node_type="document"`):
- One per file in a corpus; linked from corpus node via `contains` edge
- Stable UUID via `stable_document_id(corpus_id, filename)` — deterministic SHA-1
- `weight`: `DOCUMENT_NODE_WEIGHT = 1.0`
- Metadata stores `content_hash` (SHA-256) for idempotency checks
- `source = corpus_id`

**Chunk node** (`node_type="chunk"`):
- Raw text chunk from a document; vector-indexed for direct semantic search
- `weight`: `CHUNK_NODE_WEIGHT = 0.4`
- Top-level `document_id` Firestore field (in addition to JSON metadata) for efficient tombstoning
- `source = corpus_id`

---

## Edge (`relationships` collection)

Pydantic model: `lethe/models/node.py::Edge`

| Field | Type | Notes |
|-------|------|-------|
| `uuid` | str | Document ID |
| `subject_uuid` | str | UUID of subject Node |
| `predicate` | str | Lowercase snake_case relationship type |
| `object_uuid` | str | UUID of object Node |
| `content` | str | Human-readable triple description, default `""` |
| `weight` | float | `DEFAULT_RELATIONSHIP_WEIGHT = 0.8`; tombstoned = `0.0` |
| `domain` | str | Namespace, default `"general"` |
| `user_id` | str | Owner |
| `source` | str\|None | Provenance |
| `journal_entry_ids` | list[str] | Log node UUIDs that produced this edge |
| `created_at` | datetime\|None | |
| `updated_at` | datetime\|None | |

**Tombstoning**: When a relationship is superseded, `weight` is set to `0.0`. Tombstoned edges are excluded from traversal and search.

---

## Canonical Map (in-memory + Firestore)

Loaded at startup from Firestore config document. Stored in `app.state.canonical_map`.
- `canonical_map.node_types` — list of allowed node type strings
- `canonical_map.allowed_predicates` — list of allowed predicate strings
- Extended at runtime: new predicates proposed by LLM with `NEW:` prefix pass through a **predicate resolution gate** (§10 in algorithms.md) — an LLM evaluation checks whether an existing predicate already covers the relationship. Only confirmed novel predicates are written to Firestore and appended to the in-memory list.

---

## Default Constants (`lethe/constants.py`)

| Constant | Value | Purpose |
|----------|-------|---------|
| `DEFAULT_USER_ID` | `"global"` | Default user scope |
| `DEFAULT_DOMAIN` | `"general"` | Default domain namespace |
| `DEFAULT_NODE_TYPE` | `"generic"` | Fallback node type |
| `DEFAULT_LOG_WEIGHT` | `0.3` | Log node initial weight |
| `DEFAULT_ENTITY_WEIGHT` | `0.55` | Entity node initial weight |
| `DEFAULT_RELATIONSHIP_WEIGHT` | `0.8` | Edge initial weight |
| `LOG_NODE_HALF_LIFE_DAYS` | `30.0` | Temporal decay for log nodes |
| `STRUCTURED_NODE_HALF_LIFE_DAYS` | `365.0` | Temporal decay for entity nodes |
| `EDGE_HALF_LIFE_DAYS` | `90.0` | Temporal decay for edges |
| `TRAVERSAL_SIMILARITY_WEIGHT` | `0.7` | BFS frontier scoring: cosine weight |
| `TRAVERSAL_OBSERVATION_WEIGHT` | `0.3` | BFS frontier scoring: observation-count weight |
| `NODE_TYPE_CORPUS` | `"corpus"` | Node type for corpus hub nodes |
| `CORPUS_NODE_WEIGHT` | `1.0` | Default weight for corpus hub node |
| `NODE_TYPE_DOCUMENT` | `"document"` | Node type for document nodes |
| `DOCUMENT_NODE_WEIGHT` | `1.0` | Default weight for document node |
| `NODE_TYPE_CHUNK` | `"chunk"` | Node type for raw chunk nodes |
| `CHUNK_NODE_WEIGHT` | `0.4` | Default weight for chunk nodes |
| `DEFAULT_CHUNK_SIZE` | `600` | Default words per chunk |
| `LLM_MAX_TOKENS_PREDICATE_RESOLUTION` | `256` | Max tokens for predicate resolution gate |

---

## Document ID Strategies

- **Log nodes**: `str(uuid.uuid4())` — random UUID per ingest
- **Entity nodes**: `stable_entity_doc_id(node_type, name)` — deterministic hash of `(node_type, lowercase_name)`; ensures deduplication
- **SELF node**: `stable_self_id(user_id)` — deterministic per user
- **Corpus nodes**: `stable_corpus_node_id(corpus_id)` → `"corpus_" + sha1("corpus:" + corpus_id)`
- **Document nodes**: `stable_document_id(corpus_id, filename)` → `"doc_" + sha1(corpus_id + ":" + filename)`
- **Edges**: UUID generated at relationship creation time
