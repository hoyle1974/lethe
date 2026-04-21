# Research: Lethe Knowledge Graph API

**Branch**: `001-knowledge-graph-spec` | **Date**: 2026-04-16

This document consolidates all architectural decisions derived from reading the existing codebase.
All NEEDS CLARIFICATION items from the Technical Context were fully resolved from the code.

---

## D-001: Persistence Backend

**Decision**: Google Cloud Firestore (native async Python client)

**Rationale**: Firestore provides built-in vector ANN search (`find_nearest`) eliminating the need
for a separate vector database tier. The async client (`AsyncClient`) integrates directly with
Python asyncio and FastAPI's event loop. Firestore's document model maps naturally to the
Node/Edge domain objects.

**Alternatives considered**:
- PostgreSQL + pgvector: requires separate managed database; more operational overhead
- Pinecone / Weaviate: separate vector store with no document model; adds a second persistence tier
- In-memory dict: not durable; cannot survive restarts

---

## D-002: Two-Collection Data Model

**Decision**: `nodes` collection for entity and log nodes; `relationships` collection for edges

**Rationale**: Separating edges allows each collection to have its own Firestore vector index,
enabling independent vector search over relationship text. The `execute_search` function queries
both collections in parallel using `asyncio.gather`. Combining them into one collection would
require a `node_type != "edge"` filter that blocks the Firestore `find_nearest` index.

**Alternatives considered**:
- Single collection with discriminator field: Firestore ANN indexes cannot filter by field and
  do vector search simultaneously in the same query
- Subcollections per node: complicates cross-entity queries and prevents relationship-level search

---

## D-003: Content-Hash Document IDs

**Decision**: Entity nodes: `"entity_" + SHA1(node_type + ":" + lowercase_name)`.
Relationship nodes: `"rel_" + SHA1(subject_id + ":" + predicate + ":" + object_id)`.

**Rationale**: Deterministic IDs allow idempotent creates without a preliminary existence check in
most cases. Combined with Firestore transactions (`@firestore.async_transactional`) on the final
write, this prevents duplicate creation under concurrent ingestion.

**Limitations**: The window between the pre-transaction name_key query and the transaction commit
is not fully atomic. A sub-millisecond race could still produce two entity nodes. This is accepted
as an unlikely edge case for a personal knowledge store.

---

## D-004: Four-Path Entity Resolution

**Decision**: `ensure_node` resolution order:
1. Vector ANN search (within `lethe_entity_threshold = 0.15` cosine distance)
2. SHA1 stable doc ID direct lookup
3. `name_key` exact-match query
4. Firestore transactional create

**Rationale**: The ANN path catches aliases and spelling variants. The SHA1 and name_key paths
handle exact matches cheaply. All paths converge to transactional create as the safe fallback.
Collision detection (via LLM) applies when a semantically similar node is found in step 1.

---

## D-005: Gemini Models via Vertex AI

**Decision**: `gemini-2.5-flash` (LLM) + `text-embedding-005` (768-dim embeddings)

**Rationale**: Gemini 2.5 Flash has a large context window sufficient for the extraction prompt
(node types + predicates + full text). `text-embedding-005` is the current Vertex AI recommended
model for retrieval document/query asymmetric embedding.

**Token budget per call**:
- Extraction: up to 32,768 output tokens
- Graph summary (draft + final): 2,048 each
- Consolidation: 1,024
- Fact collision: 16 (binary "update"/"insert")
- Relationship supersession: 64 (UUID or "none")
- Thought queries: 512

---

## D-006: Temporal Decay + Reinforcement Scoring

**Decision**: `effective_distance = cosine_distance / (decay × reinforcement)`

```
decay       = 0.5 ^ (age_days / half_life)
reinforcement = 1 + 0.05 × min(len(journal_entry_ids), 50)
half_lives  = { log: 30d, entity: 365d, edge: 90d }
```

**Rationale**: Lower effective distance = higher relevance rank. Decay ensures log nodes fade
quickly relative to stable entity facts. Reinforcement rewards frequently-mentioned nodes, acting
as a surrogate for importance.

---

## D-007: Firestore Transactions for Node Creation

**Decision**: Both `ensure_node` and `create_relationship_node` use `@firestore.async_transactional`
for the final write. Pre-transaction reads (ANN search, name_key query) happen outside the
transaction because Firestore async transactions do not support queries inside them.

**Rationale**: Without transactions, two concurrent ingestions of the same entity string could
both pass the "not found" checks and both attempt to create the same document, producing an
inconsistent state.

---

## D-008: Jinja2 Prompt Templates

**Decision**: Extraction prompt rendered from `prompts/refinery.txt` (Jinja2). Template is loaded
once at first call and cached in a module-level variable.

**Rationale**: Decouples prompt text from Python code. The template receives the live `node_types`
and `allowed_predicates` from the canonical map, so every extraction call reflects the current
vocabulary without code changes.

---

## D-009: Protocol-based Infra Abstractions

**Decision**: `Embedder` and `LLMDispatcher` are `runtime_checkable` Protocols, not base classes.

**Rationale**: Protocol-based design means test mocks (`MockEmbedder`, `MockLLM` in `conftest.py`)
do not need to inherit from anything — they simply implement the required methods. This avoids
coupling tests to production class hierarchies and keeps the mock implementations minimal.

---

## Summary: All Unknowns Resolved

| Technical Context Field | Resolved Value |
|------------------------|---------------|
| Language/Version | Python 3.14 |
| Primary dependencies | FastAPI, uvicorn, google-cloud-firestore, google-cloud-aiplatform, pydantic, jinja2 |
| Storage | Firestore (two collections + one config collection) |
| Testing | pytest + pytest-asyncio (asyncio_mode = auto) |
| Target platform | Linux server / GCP |
| Project type | HTTP REST API (web service) |
| Performance goals | Async throughout; ANN pool bounded at 200 candidates |
| Constraints | user_id isolation; entity threshold 0.15; similarity threshold 0.25 |
| Scale/scope | Personal / small-team knowledge assistant |
