# API Contracts: Lethe Knowledge Graph API

**Branch**: `001-knowledge-graph-spec` | **Date**: 2026-04-16
**Base URL**: `http://<host>:<port>` (no versioning prefix at host level; version in path)

All request and response bodies are JSON. All endpoints are async.

---

## Health

### GET /v1/health

Returns server liveness. Does not check Firestore or LLM connectivity.

**Response 200**
```json
{ "status": "ok" }
```

---

## Schema / Vocabulary

### GET /v1/node-types

Returns the current canonical node type and predicate vocabulary.

**Response 200**
```json
{
  "node_types": ["person", "place", "event", "project", "goal", "preference", "asset", "tool", "generic", "log"],
  "allowed_predicates": ["works_at", "lives_in", "knows", "is_part_of", "owns", "uses",
                          "participates_in", "located_at", "created_by", "manages",
                          "reports_to", "related_to", "is_a"]
}
```

---

## Ingest

### POST /v1/ingest

Ingest free-text into the knowledge graph.

**Request**
```json
{
  "text": "Alice works at Acme Corp and lives in Seattle.",
  "domain": "general",
  "source": "chat-session-42",
  "user_id": "global",
  "timestamp": null
}
```

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `text` | yes | string | Free-text input |
| `domain` | no | string | Default: `"general"` |
| `source` | no | string\|null | Provenance label |
| `user_id` | no | string | Default: `"global"` |
| `timestamp` | no | ISO-8601 datetime\|null | Override creation time |

**Response 200**
```json
{
  "entry_uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "nodes_created": ["entity_a1b2c3..."],
  "nodes_updated": ["entity_d4e5f6..."],
  "relationships_created": ["rel_abc123..."]
}
```

**Notes**:
- Always returns 200; errors during triple processing are logged and skipped, not surfaced.
- `nodes_created`, `nodes_updated`, `relationships_created` may be empty if no triples extracted.
- A log node is always created even when triples cannot be extracted.

---

## Search

### POST /v1/search

Perform semantic vector search over nodes and edges.

**Request**
```json
{
  "query": "Where does Alice work?",
  "node_types": ["person", "place"],
  "domain": null,
  "user_id": "global",
  "limit": 20,
  "min_significance": 0.0
}
```

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `query` | yes | string | Natural-language query |
| `node_types` | no | string[] | Default: `[]` (all non-log types) |
| `domain` | no | string\|null | Domain filter |
| `user_id` | no | string | Default: `"global"` |
| `limit` | no | integer | Default: `20`; applies to each collection separately |
| `min_significance` | no | float | Default: `0.0`; minimum `weight` |

**Response 200**
```json
{
  "nodes": [
    {
      "uuid": "entity_a1b2c3",
      "node_type": "person",
      "content": "Alice",
      "domain": "entity",
      "weight": 0.55,
      "metadata": "{}",
      "journal_entry_ids": ["3fa85f64-..."],
      "name_key": "alice",
      "user_id": "global",
      "source": null,
      "created_at": "2026-04-16T10:00:00+00:00",
      "updated_at": "2026-04-16T10:00:00+00:00"
    }
  ],
  "edges": [
    {
      "uuid": "rel_abc123",
      "subject_uuid": "entity_a1b2c3",
      "predicate": "works_at",
      "object_uuid": "entity_d4e5f6",
      "content": "Alice works_at Acme Corp",
      "weight": 0.8,
      "domain": "general",
      "user_id": "global",
      "source": null,
      "journal_entry_ids": ["3fa85f64-..."],
      "created_at": "2026-04-16T10:00:00+00:00",
      "updated_at": "2026-04-16T10:00:00+00:00"
    }
  ],
  "count": 2
}
```

**Notes**:
- `embedding` field is never included in responses.
- Results ranked by temporal-decay-adjusted cosine distance (ascending effective distance = higher relevance).
- `count` is always `len(nodes) + len(edges)`.

---

## Graph

### POST /v1/graph/expand

Expand a subgraph by BFS from seed node UUIDs.

**Request**
```json
{
  "seed_ids": ["entity_a1b2c3"],
  "query": "Alice's work context",
  "hops": 2,
  "limit_per_edge": 20,
  "self_seed_neighbor_floor": 40,
  "debug": true,
  "user_id": "global"
}
```

**Response 200**
```json
{
  "nodes": {
    "entity_a1b2c3": { "uuid": "entity_a1b2c3", "node_type": "person", "content": "Alice", ... },
    "entity_d4e5f6": { "uuid": "entity_d4e5f6", "node_type": "place", "content": "Acme Corp", ... }
  },
  "edges": [
    { "uuid": "rel_abc123", "subject_uuid": "entity_a1b2c3", "predicate": "works_at", "object_uuid": "entity_d4e5f6", ... }
  ]
}
```

**Notes**:
- Log nodes may appear in `nodes` but are not used as frontier for further expansion.
- Tombstoned nodes (weight 0.0) are excluded from `nodes` and not expanded.
- Tombstoned edges (weight 0.0) are excluded from `edges`.
- `debug` field is accepted but only affects the `/v1/graph/summarize` response.

---

### POST /v1/graph/summarize

Generate an LLM-grounded natural-language summary of a graph neighbourhood.

**Request**: Same schema as `GraphExpandRequest` (above).

**Response 200**
```json
{
  "summary": "## Profile\n\nAlice is a software engineer at Acme Corp...\n\n## Work & Projects\n...",
  "debug_reasoning": {
    "query": "Alice",
    "broad_query_mode": true,
    "question_query_mode": false,
    "seed_ids": ["entity_a1b2c3"],
    "target_queries": ["Acme Corp projects", "Alice's team"],
    "retrieval_seed_ids": ["entity_x1y2z3"],
    "pass1": {
      "nodes": 12,
      "edges": 8,
      "draft_summary_chars": 420,
      "thought_response": "Acme Corp projects\nAlice's team"
    },
    "pass2": {
      "performed": true,
      "expanded_target_count": 2,
      "nodes": 15,
      "edges": 11
    },
    "final": {
      "summary_chars": 890
    }
  }
}
```

**Notes**:
- `debug_reasoning` is `null` when `debug=false`.
- Query mode auto-detected: ≤2 words → broad profile; question words / `?` → Q&A; else → free-form.
- Summary is always non-empty (automatic retry if < 100 chars).

---

## Admin

### POST /v1/admin/backfill

Backfill vector embeddings for nodes that lack them.

**Request**
```json
{ "limit": 100 }
```

**Response 200**
```json
{ "backfilled": 42 }
```

---

### POST /v1/admin/consolidate

Distil recent log entries into durable factual statements.

**Request**
```json
{ "user_id": "global" }
```

**Response 200**
```json
{
  "statements": [
    "Alice works at Acme Corp as a senior engineer.",
    "Alice lives in Seattle and has a dog named Max."
  ],
  "ingest_results": [
    {
      "entry_uuid": "uuid-1",
      "nodes_created": [],
      "nodes_updated": ["entity_a1b2c3"],
      "relationships_created": ["rel_abc123"]
    },
    {
      "entry_uuid": "uuid-2",
      "nodes_created": ["entity_max99"],
      "nodes_updated": ["entity_a1b2c3"],
      "relationships_created": ["rel_xyz789"]
    }
  ]
}
```

---

## Error Handling

The API does not define custom error schemas beyond FastAPI defaults. All 422 Unprocessable Entity
responses follow FastAPI's standard validation error format. Internal processing errors during
ingestion (triple extraction failures, individual triple processing errors) are logged and skipped
rather than surfaced as HTTP errors — the endpoint always returns 200 with partial results.
