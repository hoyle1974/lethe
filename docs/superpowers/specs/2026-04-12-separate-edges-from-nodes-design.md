# Separate Edges from Nodes

**Date:** 2026-04-12
**Status:** Approved

## Problem

Lethe currently stores relationship edges as nodes in the same `nodes` Firestore collection as entities and log entries. This causes several problems:

- `ensure_node()` is ~400 lines because it must defensively guard against relationship nodes appearing in entity lookups
- Graph traversal issues two different query types per frontier node (`_get_incoming_spo_edges` + `_get_nodes_linking_to`) because edges are disguised as nodes
- `entity_links` is a denormalized backlink array on entity nodes, maintained as a side effect of ingestion, that exists solely because you cannot query "all edges touching this entity" without it
- `hot_edges` is a bounded 20-slot cache of relationship UUIDs on entity nodes, maintained with eviction logic, that adds write complexity with unclear read benefit
- Search results mix edges and entities with no structural distinction in the API response

## Approach

**Two collections, unified search (Option A).**

Move relationship edges to a dedicated `relationships` Firestore collection with its own vector index. Entity nodes and log entries stay in `nodes`. Search queries both collections in parallel and returns typed results. Traversal queries `relationships` directly by `subject_uuid` / `object_uuid`.

This is a greenfield change — no data migration required.

---

## Data Model

### `relationships` collection

Document ID: `rel_<sha1(subject_uuid:predicate:object_uuid)>` — stable, deterministic, idempotent on re-observation.

```
relationships/{uuid}
  uuid:               str         # "rel_<sha1>"
  subject_uuid:       str         # FK → nodes/{uuid}
  predicate:          str         # "works_at", "knows", etc.
  object_uuid:        str         # FK → nodes/{uuid}
  content:            str         # "Alice works_at Acme Corp" — embedded for search
  embedding:          Vector(768) # Always required; collection is searchable
  weight:             float       # 0.0 = tombstone
  domain:             str
  user_id:            str
  source:             str | null
  journal_entry_ids:  [str]       # Source log entry UUIDs
  created_at:         timestamp
  updated_at:         timestamp
```

### `nodes` collection (updated)

Entity nodes and log entries only. The following fields are **removed**:

| Field | Reason |
|---|---|
| `predicate` | Belongs to edges |
| `subject_uuid` | Belongs to edges |
| `object_uuid` | Belongs to edges |
| `entity_links` | Replaced by querying `relationships` by subject/object UUID |
| `hot_edges` | Denormalized cache, no longer needed |
| `relevance_score` | Was used only by `hot_edges` eviction |

Valid `node_type` values going forward: `"entity"`, `"log"`, and user-defined semantic types. The `"relationship"` node type is retired.

---

## Search

### Query phase

Two vector queries fire in parallel via `asyncio.gather()`:

```python
nodes_results = FindNearest(nodes, query_vec, top_N, filter: user_id, node_type, domain)
edges_results = FindNearest(relationships, query_vec, top_N, filter: user_id, domain)
```

### Merge phase

RRF fusion + temporal decay applied to the combined candidate pool.

Temporal decay half-lives:

| Type | Half-life |
|---|---|
| Log nodes | 30 days |
| Entity nodes | 365 days |
| Relationship edges | 90 days |

Relationship edges are more durable than episodic memories but can go stale (job changes, relationship endings), so 90 days is appropriate.

### API response

`SearchResponse` returns typed results in two separate fields:

```python
class SearchResponse(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
```

`Edge` is a first-class response type, not serialized to look like a node. Callers always know what they received.

```python
class Edge(BaseModel):
    uuid: str
    subject_uuid: str
    predicate: str
    object_uuid: str
    content: str
    weight: float
    domain: str
    user_id: str
    source: str | None
    journal_entry_ids: list[str]
    created_at: datetime
    updated_at: datetime
```

This model is shared between `SearchResponse` and the expand response (`ExpandResponse`).

---

## Traversal

### BFS expansion (per frontier node)

Two queries to `relationships`, fired in parallel:

```python
outgoing, incoming = await asyncio.gather(
    query(relationships, subject_uuid == node.uuid, user_id == user_id),
    query(relationships, object_uuid == node.uuid, user_id == user_id),
)
```

From returned edges, collect the union of connected entity UUIDs (the "other side" of each edge), subtract already-visited nodes, and that becomes the next frontier. Batch-fetch frontier nodes from `nodes`.

The semaphore(10) rate limit on concurrent Firestore queries is unchanged.

### Semantic pruning

Unchanged — cosine similarity + observation count scoring applied to the frontier after BFS expansion.

### Expand response

`edges` in the expand response come directly from `relationships` documents. No reconstruction from node fields. The `Edge` model is shared between search and expand responses.

---

## Ingest Pipeline

### `create_relationship_node()`

Writes to `relationships` collection instead of `nodes`. SHA1 stable ID logic unchanged. Firestore transaction unchanged. Embedding the relationship content is now always required. Previously, some code paths wrote relationship nodes without embeddings if the embedding call was skipped during fast-path deduplication. All writes to `relationships` must include a valid `embedding` field or the document is rejected.

### `ensure_node()`

Strictly entity-focused. Remove:
- All defensive guards checking whether a found node is a relationship type
- The `hot_edges` update at the end of successful entity resolution

The four-stage resolution cascade (stable ID → name_key → semantic similarity → create) is unchanged. Estimated reduction: ~400 lines → ~280 lines.

### `add_entity_link()`

Deleted. Traversal no longer relies on the `entity_links` backlink array. The ingest pipeline no longer calls it.

---

## Firestore Indexes

New indexes required on the `relationships` collection:

| Fields | Purpose |
|---|---|
| `user_id`, `subject_uuid` | Traversal: outgoing edges from a node |
| `user_id`, `object_uuid` | Traversal: incoming edges to a node |
| `user_id`, `domain`, `embedding` | Vector search with domain filter |
| `user_id`, `embedding` | Vector search without domain filter |

These mirror the existing indexes on `nodes`.

---

## Testing

### Changes required

| Test file | Change |
|---|---|
| `test_traverse.py` | Rewrite mock setup to create documents in `relationships` instead of relationship-type nodes in `nodes` |
| `test_search.py` | Assert `SearchResponse` has `nodes` and `edges` fields; add tests for cross-collection merge and RRF fusion with mixed result types |
| `test_ensure_node.py` | Remove tests guarding against relationship nodes in entity lookups; add test asserting `ensure_node` never touches `relationships` |
| `test_ingest.py` | Assert relationship documents land in `relationships` and entity documents in `nodes`; assert `entity_links` absent from all nodes |
| `conftest.py` | `MockFirestoreClient` exposes both `nodes` and `relationships` collections |

---

## Files Affected

| File | Change |
|---|---|
| `lethe/graph/ensure_node.py` | Remove hot_edges update, relationship-type guards; entity-only |
| `lethe/graph/ingest.py` | Write relationships to new collection; delete `add_entity_link()` call |
| `lethe/graph/traverse.py` | Replace dual-query BFS with single `relationships` collection queries |
| `lethe/graph/search.py` | Parallel query both collections; merge with RRF; return typed `SearchResponse` |
| `lethe/infra/firestore.py` | Add `relationships_collection()` helper mirroring existing `nodes_collection()` |
| `lethe/models/` | Add `Edge` response model; update `SearchResponse`, `ExpandResponse` |
| `lethe/types.py` | Remove `"relationship"` from `CoreNodeType` |
| `firestore.indexes.json` | Add indexes for `relationships` collection |
| `tests/conftest.py` | Add `relationships` mock collection |
| `tests/test_*.py` | As described above |
