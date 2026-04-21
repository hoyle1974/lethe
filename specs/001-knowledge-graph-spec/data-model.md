# Data Model: Lethe Knowledge Graph API

**Branch**: `001-knowledge-graph-spec` | **Date**: 2026-04-16

---

## Overview

Lethe uses two Firestore collections and one config collection:

| Collection | Purpose |
|-----------|---------|
| `nodes` (configurable) | Entity nodes and episodic log nodes |
| `relationships` (configurable) | Directed SPO relationship edges |
| `_config` | Canonical map (node types + predicates) |

---

## Entity: Node

Stored in: `nodes` collection
Pydantic model: `lethe.models.node.Node`

| Field | Type | Firestore | Description |
|-------|------|-----------|-------------|
| `uuid` | `str` | Document ID | Deterministic hash ID (entity) or random UUID (log) |
| `node_type` | `str` | Field | Vocabulary-controlled type: `person`, `place`, `event`, `project`, `goal`, `preference`, `asset`, `tool`, `generic`, `log` |
| `content` | `str` | Field | Human-readable text representing this node |
| `domain` | `str` | Field | Namespace: `"general"`, `"entity"`, `"core_memory"`, user-defined |
| `weight` | `float` | Field | Relevance weight; 0.55 (entity default), 0.3 (log default). 0.0 = tombstoned |
| `metadata` | `str` | Field | JSON string for arbitrary extra data (schema-less) |
| `journal_entry_ids` | `list[str]` | Field (array) | Log entry UUIDs that reference this node (reinforcement signal) |
| `name_key` | `str \| None` | Field | Lowercase normalised name for exact-match deduplication |
| `user_id` | `str` | Field | Owner user identifier (isolation key) |
| `source` | `str \| None` | Field | Optional provenance string supplied by caller |
| `created_at` | `datetime \| None` | Field | UTC ISO timestamp of creation |
| `updated_at` | `datetime \| None` | Field | UTC ISO timestamp of last update |
| `embedding` | `list[float] \| None` | Field (vector) | 768-dim Gemini embedding; excluded from API responses |

### Node ID Schemes

| node_type | ID formula |
|-----------|-----------|
| `"log"` | `str(uuid.uuid4())` — random UUID |
| Entity types | `"entity_" + SHA1(node_type + ":" + lowercase_name)` |
| Self-node | `"entity_" + SHA1("self:" + user_id)` |

### Default Weights

| node_type | Default weight | Half-life |
|-----------|---------------|-----------|
| `"log"` | 0.3 | 30 days |
| All entity types | 0.55 | 365 days |
| Tombstoned | 0.0 | N/A (excluded) |

---

## Entity: Edge

Stored in: `relationships` collection
Pydantic model: `lethe.models.node.Edge`

| Field | Type | Firestore | Description |
|-------|------|-----------|-------------|
| `uuid` | `str` | Document ID | `"rel_" + SHA1(subject_id + ":" + predicate + ":" + object_id)` |
| `subject_uuid` | `str` | Field | UUID of the subject Node |
| `predicate` | `str` | Field | Normalised relationship verb (lowercase snake_case) |
| `object_uuid` | `str` | Field | UUID of the object Node |
| `content` | `str` | Field | Denormalised `"subject_content predicate object_content"` for embedding |
| `weight` | `float` | Field | Relevance weight; 0.8 default. 0.0 = tombstoned |
| `domain` | `str` | Field | Namespace (inherits from ingest request) |
| `user_id` | `str` | Field | Owner user identifier (isolation key) |
| `source` | `str \| None` | Field | Optional provenance string |
| `journal_entry_ids` | `list[str]` | Field (array) | Log entry UUIDs that produced this edge |
| `created_at` | `datetime \| None` | Field | UTC ISO timestamp |
| `updated_at` | `datetime \| None` | Field | UTC ISO timestamp |
| (no explicit embedding field in model) | `vector` | Firestore only | 768-dim; stored in Firestore, not returned in API |

### Edge Half-life

| Default weight | Half-life |
|---------------|-----------|
| 0.8 | 90 days |
| 0.0 | tombstoned — excluded from traversal and search |

### Predicate Normalisation

Raw LLM predicate → `re.sub(r"[\s\-]+", "_", p.strip()).lower()`

Examples:
- `"works at"` → `"works_at"`
- `"NEW: moved-to"` → `"moved_to"` (NEW: prefix stripped)
- `"Is_A"` → `"is_a"`

---

## Entity: CanonicalMap

Stored in: `_config/canonical_map` Firestore document

| Field | Type | Description |
|-------|------|-------------|
| `node_types` | `list[str]` | Vocabulary of valid node type identifiers |
| `allowed_predicates` | `list[str]` | Vocabulary of valid predicate strings |

### Defaults

**Default node types** (10):
`person`, `place`, `event`, `project`, `goal`, `preference`, `asset`, `tool`, `generic`, `log`

**Default predicates** (13):
`works_at`, `lives_in`, `knows`, `is_part_of`, `owns`, `uses`, `participates_in`,
`located_at`, `created_by`, `manages`, `reports_to`, `related_to`, `is_a`

---

## Request / Response Schemas

### IngestRequest

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | `str` | required | Free-text to ingest |
| `domain` | `str` | `"general"` | Knowledge domain namespace |
| `source` | `str \| None` | `null` | Optional provenance label |
| `user_id` | `str` | `"global"` | Owner user identifier |
| `timestamp` | `datetime \| None` | `null` | Override creation timestamp |

### IngestResponse

| Field | Type | Description |
|-------|------|-------------|
| `entry_uuid` | `str` | UUID of the created log node |
| `nodes_created` | `list[str]` | UUIDs of newly created entity nodes |
| `nodes_updated` | `list[str]` | UUIDs of updated entity nodes |
| `relationships_created` | `list[str]` | UUIDs of created/updated edges |

### SearchRequest

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | `str` | required | Natural-language search query |
| `node_types` | `list[str]` | `[]` | Filter to these node types; empty = all non-log |
| `domain` | `str \| None` | `null` | Filter to this domain |
| `user_id` | `str` | `"global"` | Owner filter |
| `limit` | `int` | `20` | Max results per collection |
| `min_significance` | `float` | `0.0` | Minimum weight filter |

### SearchResponse

| Field | Type | Description |
|-------|------|-------------|
| `nodes` | `list[Node]` | Ranked matching nodes |
| `edges` | `list[Edge]` | Ranked matching edges |
| `count` | `int` | `len(nodes) + len(edges)` (auto-computed) |

### GraphExpandRequest

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `seed_ids` | `list[str]` | required | Starting node UUIDs for BFS |
| `query` | `str \| None` | `null` | Optional focus query for frontier pruning |
| `hops` | `int` | `2` | Number of BFS hops |
| `limit_per_edge` | `int` | `20` | Max frontier nodes per hop after pruning |
| `self_seed_neighbor_floor` | `int` | `40` | Min SELF neighbours to preserve at hop 0 |
| `debug` | `bool` | `true` | Include debug_reasoning in summarize response |
| `user_id` | `str` | `"global"` | Owner filter |

### GraphExpandResponse

| Field | Type | Description |
|-------|------|-------------|
| `nodes` | `dict[str, Node]` | UUID → Node map of all discovered nodes |
| `edges` | `list[Edge]` | All edges traversed (deduped by UUID) |

### GraphSummarizeResponse

| Field | Type | Description |
|-------|------|-------------|
| `summary` | `str` | LLM-generated markdown summary |
| `debug_reasoning` | `dict \| None` | Debug detail (query mode, pass 1/2 stats, thought queries) |

---

## State Transitions

### Node / Edge Weight Lifecycle

```
[created]  weight = default (0.55 entity / 0.3 log / 0.8 edge)
     │
     ├── ingestion references it → journal_entry_ids grows → reinforcement ↑
     │
     └── relationship superseded → weight → 0.0 (tombstoned)
                                         ↓
                            excluded from search and traversal
                            (no delete; tombstone is permanent)
```

### Predicate Lifecycle

```
[LLM extracts triple with existing predicate]
  → normalized_predicate() → use directly

[LLM extracts triple with "NEW: <predicate>"]
  → strip prefix → normalized_predicate()
  → append_predicate() → ArrayUnion into _config/canonical_map
  → in-memory CanonicalMap updated for current request
```
