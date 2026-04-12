# Separate Edges from Nodes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move relationship edges out of the `nodes` Firestore collection into a dedicated `relationships` collection, eliminating the nodes-as-edges data model confusion.

**Architecture:** A new `relationships` Firestore collection stores SPO edges with their own vector index. Entity nodes and log entries remain in `nodes`. Search queries both collections in parallel. BFS traversal queries `relationships` directly by `subject_uuid`/`object_uuid`. The full `Edge` Pydantic model replaces the current stub, and `SearchResponse` returns `nodes` and `edges` as separate typed fields.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, google-cloud-firestore (async), pytest-asyncio

---

## File Map

| File | Tasks |
|---|---|
| `lethe/config.py` | 1 |
| `lethe/constants.py` | 1 |
| `lethe/models/node.py` | 2, 3, 8 |
| `lethe/graph/ensure_node.py` | 2, 4, 5 |
| `lethe/graph/search.py` | 3 |
| `lethe/routers/search.py` | 3 |
| `lethe/routers/graph.py` | 2, 3 |
| `lethe/graph/ingest.py` | 5 |
| `lethe/graph/traverse.py` | 6 |
| `lethe/types.py` | 8 |
| `firestore.indexes.json` | 9 |
| `tests/test_config.py` | 1 |
| `tests/test_node_models.py` | 2, 8 |
| `tests/test_ensure_node.py` | 2 |
| `tests/test_search.py` | 3 |
| `tests/test_routers.py` | 2, 3 |
| `tests/test_traverse.py` | 6 |

---

## Task 1: Config + Constants

**Files:**
- Modify: `lethe/config.py`
- Modify: `lethe/constants.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_config.py`:

```python
def test_config_relationships_collection_default():
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True):
        from lethe.config import Config
        cfg = Config()
        assert cfg.lethe_relationships_collection == "relationships"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/pytest tests/test_config.py::test_config_relationships_collection_default -v
```
Expected: `FAILED` — `Config` has no attribute `lethe_relationships_collection`

- [ ] **Step 3: Add field to Config**

In `lethe/config.py`, add after `lethe_collection`:

```python
lethe_relationships_collection: str = "relationships"
```

- [ ] **Step 4: Add constant**

In `lethe/constants.py`, add after `STRUCTURED_NODE_HALF_LIFE_DAYS`:

```python
EDGE_HALF_LIFE_DAYS = 90.0
```

- [ ] **Step 5: Run test to verify it passes**

```bash
./.venv/bin/pytest tests/test_config.py -v
```
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add lethe/config.py lethe/constants.py tests/test_config.py
git commit -m "feat: add lethe_relationships_collection config and EDGE_HALF_LIFE_DAYS constant"
```

---

## Task 2: Full Edge Model + `doc_to_edge()`

The current `Edge` model only has `subject`, `predicate`, `object`. Expand it to a full document model and add a `doc_to_edge()` conversion function. Update all code that constructs `Edge` objects.

**Files:**
- Modify: `lethe/models/node.py`
- Modify: `lethe/graph/ensure_node.py`
- Modify: `lethe/routers/graph.py`
- Modify: `tests/test_node_models.py`
- Modify: `tests/test_ensure_node.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_ensure_node.py`:

```python
def test_doc_to_edge_populates_all_fields():
    from lethe.graph.ensure_node import doc_to_edge
    data = {
        "subject_uuid": "entity_aaa",
        "predicate": "works_at",
        "object_uuid": "entity_bbb",
        "content": "Alice works_at Acme",
        "weight": 0.8,
        "domain": "general",
        "user_id": "global",
        "source": None,
        "journal_entry_ids": ["log_1"],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
    }
    edge = doc_to_edge("rel_abc123", data)
    assert edge.uuid == "rel_abc123"
    assert edge.subject_uuid == "entity_aaa"
    assert edge.predicate == "works_at"
    assert edge.object_uuid == "entity_bbb"
    assert edge.content == "Alice works_at Acme"
    assert edge.weight == 0.8
    assert edge.journal_entry_ids == ["log_1"]
    assert edge.created_at is not None


def test_doc_to_edge_strips_vector_distance():
    from lethe.graph.ensure_node import doc_to_edge
    data = {
        "subject_uuid": "s",
        "predicate": "p",
        "object_uuid": "o",
        "vector_distance": 0.15,  # must be stripped
    }
    edge = doc_to_edge("rel_x", data)
    assert edge.uuid == "rel_x"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./.venv/bin/pytest tests/test_ensure_node.py::test_doc_to_edge_populates_all_fields tests/test_ensure_node.py::test_doc_to_edge_strips_vector_distance -v
```
Expected: `FAILED` — `doc_to_edge` not defined

- [ ] **Step 3: Replace Edge model in `lethe/models/node.py`**

Replace the existing `Edge` class entirely:

```python
class Edge(BaseModel):
    uuid: str
    subject_uuid: str
    predicate: str
    object_uuid: str
    content: str = ""
    weight: float = DEFAULT_RELATIONSHIP_WEIGHT
    domain: str = DEFAULT_DOMAIN
    user_id: str = DEFAULT_USER_ID
    source: Optional[str] = None
    journal_entry_ids: list[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
```

You will need to add this import at the top of `lethe/models/node.py` if not already present:
```python
from lethe.constants import DEFAULT_DOMAIN, DEFAULT_USER_ID, DEFAULT_RELATIONSHIP_WEIGHT
```

The existing imports already have `DEFAULT_DOMAIN` and `DEFAULT_USER_ID` — add `DEFAULT_RELATIONSHIP_WEIGHT`.

- [ ] **Step 4: Add `doc_to_edge()` to `lethe/graph/ensure_node.py`**

First, add `Edge` to the existing import at the top of `lethe/graph/ensure_node.py`:

```python
from lethe.models.node import Edge, Node
```

Then add `doc_to_edge()` directly after `doc_to_node()`:

```python
def doc_to_edge(doc_id: str, data: dict) -> Edge:
    """Convert a Firestore relationships document to an Edge model."""
    data.pop("vector_distance", None)
    return Edge(
        uuid=doc_id,
        subject_uuid=data.get("subject_uuid", ""),
        predicate=data.get("predicate", ""),
        object_uuid=data.get("object_uuid", ""),
        content=data.get("content", ""),
        weight=float(data.get("weight", DEFAULT_RELATIONSHIP_WEIGHT)),
        domain=data.get("domain", DEFAULT_DOMAIN),
        user_id=data.get("user_id", DEFAULT_USER_ID),
        source=data.get("source"),
        journal_entry_ids=list(data.get("journal_entry_ids", [])),
        created_at=parse_to_utc(data.get("created_at")),
        updated_at=parse_to_utc(data.get("updated_at")),
    )
```

- [ ] **Step 5: Update `GraphExpandResponse.to_markdown()` in `lethe/models/node.py`**

Change the two references from `edge.subject` / `edge.object` to `edge.subject_uuid` / `edge.object_uuid`:

```python
for edge in self.edges:
    subj = self.nodes.get(edge.subject_uuid)
    obj = self.nodes.get(edge.object_uuid)
    subj_label = subj.content[:40] if subj else edge.subject_uuid[:8]
    obj_label = obj.content[:40] if obj else edge.object_uuid[:8]
    lines.append(f"- {subj_label} --[{edge.predicate}]--> {obj_label}")
```

- [ ] **Step 6: Update `_merge_graphs()` in `lethe/routers/graph.py`**

Change the edge deduplication key from `(e.subject, e.predicate, e.object)` to use the new field names:

```python
def _merge_graphs(base: GraphExpandResponse, extra: GraphExpandResponse) -> GraphExpandResponse:
    merged_nodes = dict(base.nodes)
    merged_nodes.update(extra.nodes)
    merged_edges = list(base.edges)
    seen_edges = {(e.subject_uuid, e.predicate, e.object_uuid) for e in merged_edges}
    for edge in extra.edges:
        key = (edge.subject_uuid, edge.predicate, edge.object_uuid)
        if key not in seen_edges:
            seen_edges.add(key)
            merged_edges.append(edge)
    return GraphExpandResponse(nodes=merged_nodes, edges=merged_edges)
```

- [ ] **Step 7: Update `tests/test_node_models.py`**

Update `test_graph_expand_response` and `test_graph_expand_to_markdown` to use the new Edge constructor (requires `uuid`, `subject_uuid`, `object_uuid` instead of `subject`, `object`):

```python
def test_graph_expand_response():
    r = GraphExpandResponse(
        nodes={"uuid1": Node(uuid="uuid1", node_type="person", content="Alice")},
        edges=[Edge(uuid="rel_001", subject_uuid="uuid1", predicate="works_at", object_uuid="uuid2")],
    )
    assert len(r.nodes) == 1
    assert len(r.edges) == 1


def test_graph_expand_to_markdown():
    r = GraphExpandResponse(
        nodes={
            "s1": Node(uuid="s1", node_type="person", content="Alice"),
            "o1": Node(uuid="o1", node_type="generic", content="Acme Corp"),
        },
        edges=[Edge(uuid="rel_001", subject_uuid="s1", predicate="works_at", object_uuid="o1")],
    )
    md = r.to_markdown(seed_ids=["s1"])
    assert "Alice" in md
    assert "works_at" in md
    assert "[SEED]" in md
```

- [ ] **Step 8: Run all tests**

```bash
./.venv/bin/pytest tests/ -v
```
Expected: all PASS (the old `Edge(subject=..., object=...)` tests are now updated)

- [ ] **Step 9: Commit**

```bash
git add lethe/models/node.py lethe/graph/ensure_node.py lethe/routers/graph.py \
        tests/test_node_models.py tests/test_ensure_node.py
git commit -m "feat: expand Edge to full document model, add doc_to_edge()"
```

---

## Task 3: SearchResponse + Parallel Edge Search

Update `SearchResponse` to return `nodes` and `edges` separately. Update `execute_search()` to query both collections in parallel. Update both routers.

**Files:**
- Modify: `lethe/models/node.py`
- Modify: `lethe/graph/search.py`
- Modify: `lethe/routers/search.py`
- Modify: `lethe/routers/graph.py`
- Modify: `tests/test_search.py`
- Modify: `tests/test_routers.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_search.py`:

```python
def test_search_response_has_nodes_and_edges_fields():
    from lethe.models.node import Edge, SearchResponse
    r = SearchResponse(
        nodes=[Node(uuid="n1", node_type="entity", content="Alice")],
        edges=[Edge(uuid="rel_1", subject_uuid="n1", predicate="works_at", object_uuid="n2")],
    )
    assert len(r.nodes) == 1
    assert len(r.edges) == 1
    assert r.count == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/pytest tests/test_search.py::test_search_response_has_nodes_and_edges_fields -v
```
Expected: `FAILED` — `SearchResponse` has no `nodes` or `edges` fields

- [ ] **Step 3: Update `SearchResponse` in `lethe/models/node.py`**

Replace:
```python
class SearchResponse(BaseModel):
    results: list[Node]
    count: int = 0
```
With:
```python
class SearchResponse(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    count: int = 0
```

- [ ] **Step 4: Update `execute_search()` in `lethe/graph/search.py`**

Replace the entire function with a version that queries both collections in parallel. Also add `_edge_vector_search()` as a new private function. The full new content of `lethe/graph/search.py`:

```python
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

from lethe.config import Config
from lethe.constants import (
    EDGE_HALF_LIFE_DAYS,
    EMBEDDING_TASK_RETRIEVAL_QUERY,
    LOG_NODE_HALF_LIFE_DAYS,
    NODE_TYPE_ENTITY,
    NODE_TYPE_LOG,
    STRUCTURED_NODE_HALF_LIFE_DAYS,
)
from lethe.graph.ensure_node import doc_to_edge, doc_to_node, parse_to_utc
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import DistanceMeasure, FieldFilter, Vector
from lethe.models.node import Edge, Node

log = logging.getLogger(__name__)

_REINFORCEMENT_ALPHA = 0.05
_REINFORCEMENT_MAX_ENTRIES = 50
_SEARCH_POOL_MAX = 200


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def half_life_days_for_node_type(node_type: str) -> float:
    if node_type == NODE_TYPE_LOG:
        return LOG_NODE_HALF_LIFE_DAYS
    return STRUCTURED_NODE_HALF_LIFE_DAYS


def effective_distance_decay(
    node: Node,
    raw_distance: float,
    now_utc: datetime,
    reinforcement_alpha: float = _REINFORCEMENT_ALPHA,
) -> float:
    """Lower is better (cosine distance). Applies half-life decay and reinforcement offset."""
    ref = node.updated_at or node.created_at
    if ref is None:
        age_days = 0.0
    else:
        age_days = max(0.0, (now_utc - ref).total_seconds() / 86400.0)
    hl = half_life_days_for_node_type(node.node_type)
    decay_factor = 0.5 ** (age_days / hl) if hl > 0 else 1.0
    n_entries = min(len(node.journal_entry_ids), _REINFORCEMENT_MAX_ENTRIES)
    reinforcement = 1.0 + reinforcement_alpha * n_entries
    denom = decay_factor * reinforcement
    if denom <= 0.0:
        return raw_distance
    return raw_distance / denom


async def vector_search(
    db: firestore.AsyncClient,
    config: Config,
    query_vector: list[float],
    node_types: list[str],
    domain: Optional[str],
    user_id: str,
    limit: int,
) -> list[tuple[Node, float]]:
    col = db.collection(config.lethe_collection)

    filters = [FieldFilter("user_id", "==", user_id)]
    if node_types:
        filters.append(FieldFilter("node_type", "in", node_types))
    else:
        filters.append(FieldFilter("node_type", "!=", NODE_TYPE_LOG))
    if domain:
        filters.append(FieldFilter("domain", "==", domain))

    q = col
    for f in filters:
        q = q.where(filter=f)

    try:
        vq = q.find_nearest(
            vector_field="embedding",
            query_vector=Vector(query_vector),
            distance_measure=DistanceMeasure.COSINE,
            distance_result_field="vector_distance",
            limit=limit,
        )
        results: list[tuple[Node, float]] = []
        async for doc in vq.stream():
            data = doc.to_dict() or {}
            dist_raw = data.get("vector_distance", 1.0)
            try:
                raw_distance = float(dist_raw)
            except (TypeError, ValueError):
                raw_distance = 1.0
            results.append((doc_to_node(doc.id, data), raw_distance))
        log.info("vector_search: %d results for user_id=%s", len(results), user_id)
        return results
    except Exception as e:
        log.warning("vector_search failed: %s", e)
        return []


async def _edge_vector_search(
    db: firestore.AsyncClient,
    config: Config,
    query_vector: list[float],
    domain: Optional[str],
    user_id: str,
    limit: int,
) -> list[tuple[Edge, float]]:
    col = db.collection(config.lethe_relationships_collection)

    filters = [FieldFilter("user_id", "==", user_id)]
    if domain:
        filters.append(FieldFilter("domain", "==", domain))

    q = col
    for f in filters:
        q = q.where(filter=f)

    try:
        vq = q.find_nearest(
            vector_field="embedding",
            query_vector=Vector(query_vector),
            distance_measure=DistanceMeasure.COSINE,
            distance_result_field="vector_distance",
            limit=limit,
        )
        results: list[tuple[Edge, float]] = []
        async for doc in vq.stream():
            data = doc.to_dict() or {}
            dist_raw = data.get("vector_distance", 1.0)
            try:
                raw_distance = float(dist_raw)
            except (TypeError, ValueError):
                raw_distance = 1.0
            results.append((doc_to_edge(doc.id, data), raw_distance))
        log.info("_edge_vector_search: %d results for user_id=%s", len(results), user_id)
        return results
    except Exception as e:
        log.warning("_edge_vector_search failed: %s", e)
        return []


async def execute_search(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    query: str,
    node_types: list[str],
    domain: Optional[str],
    user_id: str,
    limit: int,
    min_significance: float,
) -> tuple[list[Node], list[Edge]]:
    query_vector = await embedder.embed(query, EMBEDDING_TASK_RETRIEVAL_QUERY)
    pool = min(max(limit * 5, limit), _SEARCH_POOL_MAX)

    node_scored, edge_scored = await asyncio.gather(
        vector_search(db, config, query_vector, node_types, domain, user_id, pool),
        _edge_vector_search(db, config, query_vector, domain, user_id, pool),
    )

    now_utc = datetime.now(timezone.utc)

    # Rank nodes with temporal decay
    decorated_nodes = [
        (n, effective_distance_decay(n, d, now_utc)) for n, d in node_scored
    ]
    decorated_nodes.sort(key=lambda x: x[1])
    nodes = [n for n, _ in decorated_nodes if n.weight > 0.0]
    if min_significance > 0.0:
        nodes = [n for n in nodes if n.weight >= min_significance]
    nodes = nodes[:limit]

    # Rank edges with temporal decay using EDGE_HALF_LIFE_DAYS
    decorated_edges: list[tuple[Edge, float]] = []
    for edge, raw in edge_scored:
        ref = edge.updated_at or edge.created_at
        age_days = max(0.0, (now_utc - ref).total_seconds() / 86400.0) if ref else 0.0
        decay = 0.5 ** (age_days / EDGE_HALF_LIFE_DAYS) if EDGE_HALF_LIFE_DAYS > 0 else 1.0
        n_entries = min(len(edge.journal_entry_ids), _REINFORCEMENT_MAX_ENTRIES)
        reinforcement = 1.0 + _REINFORCEMENT_ALPHA * n_entries
        denom = decay * reinforcement
        effective = raw / denom if denom > 0 else raw
        decorated_edges.append((edge, effective))
    decorated_edges.sort(key=lambda x: x[1])
    edges = [e for e, _ in decorated_edges if e.weight > 0.0]
    edges = edges[:limit]

    log.info(
        "execute_search: query=%r nodes=%d edges=%d", query, len(nodes), len(edges)
    )
    return nodes, edges
```

- [ ] **Step 5: Update `lethe/routers/search.py`**

Replace the route handler body:

```python
@router.post("/v1/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    config: Config = Depends(get_config),
) -> SearchResponse:
    nodes, edges = await execute_search(
        db=db,
        embedder=embedder,
        config=config,
        query=req.query,
        node_types=req.node_types or [],
        domain=req.domain,
        user_id=req.user_id,
        limit=req.limit,
        min_significance=req.min_significance,
    )
    return SearchResponse(nodes=nodes, edges=edges, count=len(nodes) + len(edges))
```

- [ ] **Step 6: Update `execute_search` usage in `lethe/routers/graph.py`**

In the `summarize()` handler, the loop over `search_results` currently does `for nodes in search_results: for node in nodes:`. After the change, `execute_search` returns a tuple `(nodes, edges)`. Update the loop:

```python
search_results = await asyncio.gather(
    *[
        execute_search(
            db=db,
            embedder=embedder,
            config=config,
            query=target_query,
            node_types=[],
            domain=None,
            user_id=req.user_id,
            limit=5,
            min_significance=0.0,
        )
        for target_query in target_queries
    ]
)
seen_seed_ids: set[str] = set()
for node_list, _ in search_results:
    for node in node_list:
        if node.uuid not in seen_seed_ids:
            seen_seed_ids.add(node.uuid)
            retrieval_seed_ids.append(node.uuid)
```

- [ ] **Step 7: Update `tests/test_routers.py` search mocks**

Find every occurrence of `search_mock = AsyncMock(return_value=[Node(...)])` in `test_routers.py` and change to return a tuple:

```python
search_mock = AsyncMock(
    return_value=([Node(uuid="target-1", node_type="generic", content="Acme")], [])
)
```

There are four test functions that set `search_mock`. Update all of them:
- `test_graph_summarize_runs_iterative_reasoning_loop`
- `test_graph_summarize_debug_mode_returns_reasoning`
- `test_graph_summarize_ignores_non_uuid_thought_tokens`
- `test_graph_summarize_retries_when_final_summary_too_short`
- `test_graph_summarize_broad_query_disables_semantic_pruning`
- `test_graph_summarize_question_query_returns_answer_evidence_shape`

- [ ] **Step 8: Run all tests**

```bash
./.venv/bin/pytest tests/ -v
```
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add lethe/models/node.py lethe/graph/search.py lethe/routers/search.py \
        lethe/routers/graph.py tests/test_search.py tests/test_routers.py
git commit -m "feat: SearchResponse returns nodes+edges, execute_search queries both collections"
```

---

## Task 4: `create_relationship_node()` Writes to Relationships Collection

Redirect all relationship writes from `nodes` to `relationships`.

**Files:**
- Modify: `lethe/graph/ensure_node.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_ensure_node.py`:

```python
def test_create_relationship_node_targets_relationships_collection():
    """create_relationship_node must use lethe_relationships_collection, not lethe_collection."""
    import inspect
    import lethe.graph.ensure_node as m
    src = inspect.getsource(m.create_relationship_node)
    assert "lethe_relationships_collection" in src, (
        "create_relationship_node must reference config.lethe_relationships_collection"
    )
    # Confirm the old nodes-collection reference is gone from this function
    fn_body = src.split("def create_relationship_node")[1]
    assert "lethe_collection" not in fn_body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/pytest tests/test_ensure_node.py::test_create_relationship_node_targets_relationships_collection -v
```
Expected: `FAILED` — source still contains `lethe_collection` and not `lethe_relationships_collection`

- [ ] **Step 3: Update `create_relationship_node()` in `lethe/graph/ensure_node.py`**

Make the following changes to `create_relationship_node()`:

1. Change the collection reference from `config.lethe_collection` to `config.lethe_relationships_collection`:
```python
col = db.collection(config.lethe_relationships_collection)
```

2. Remove the `node_type` filter from the existing-facts query (the collection is relationships-only):
```python
rq = (
    col.where(filter=FieldFilter("user_id", "==", user_id))
    .where(filter=FieldFilter("subject_uuid", "==", subject_id))
    .order_by("updated_at", direction=firestore.Query.DESCENDING)
    .limit(RELATIONSHIP_SUPERSEDE_CANDIDATE_LIMIT)
)
```

3. Update `create_data` — remove `node_type`, `entity_links`, `relevance_score`, `metadata`:
```python
create_data = {
    "content": content,
    "predicate": predicate,
    "subject_uuid": subject_id,
    "object_uuid": object_id,
    "journal_entry_ids": [source_entry_id] if source_entry_id else [],
    "domain": DEFAULT_DOMAIN,
    "weight": DEFAULT_RELATIONSHIP_WEIGHT,
    "embedding": Vector(vector),
    "user_id": user_id,
    "created_at": ts,
    "updated_at": ts,
}
```

4. Update the `tombstone_relationship` call to use the relationships collection:
```python
await tombstone_relationship(db, config.lethe_relationships_collection, superseded_id, rid)
```

Remove these imports from `lethe/graph/ensure_node.py` that are no longer needed:
- `NODE_TYPE_RELATIONSHIP` (was used in `create_data` and the query filter)

- [ ] **Step 4: Run all tests**

```bash
./.venv/bin/pytest tests/ -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lethe/graph/ensure_node.py
git commit -m "feat: create_relationship_node writes to relationships collection"
```

---

## Task 5: Remove `add_entity_link()` and Clean Up Entity Links

Delete `add_entity_link()` and remove all `entity_links` fields from Firestore writes.

**Files:**
- Modify: `lethe/graph/ensure_node.py`
- Modify: `lethe/graph/ingest.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_ensure_node.py`:

```python
def test_add_entity_link_does_not_exist():
    """add_entity_link should be gone — traversal no longer uses entity_links."""
    import lethe.graph.ensure_node as m
    assert not hasattr(m, "add_entity_link"), (
        "add_entity_link still exists; remove it and its callers"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/pytest tests/test_ensure_node.py::test_add_entity_link_does_not_exist -v
```
Expected: `FAILED` — `add_entity_link` still exists

- [ ] **Step 3: Delete `add_entity_link()` from `lethe/graph/ensure_node.py`**

Remove the entire function (lines ~326–333 in the current file):

```python
# DELETE this entire function:
async def add_entity_link(
    db: firestore.AsyncClient,
    config: Config,
    node_uuid: str,
    link_uuid: str,
) -> None:
    ref = db.collection(config.lethe_collection).document(node_uuid)
    await ref.update({"entity_links": ArrayUnion([link_uuid])})
```

- [ ] **Step 4: Remove `entity_links` from new entity node creation in `ensure_node()`**

In `ensure_node()`, there are two places that write `"entity_links": []` — the SELF path and the create path. Remove both:

In the SELF path:
```python
node_data = {
    "node_type": "person",
    "content": clean,
    "name_key": clean.lower(),
    "domain": NODE_TYPE_ENTITY,
    "weight": DEFAULT_ENTITY_WEIGHT,
    "metadata": "{}",
    # REMOVED: "entity_links": [],
    "journal_entry_ids": [source_entry_id] if source_entry_id else [],
    "embedding": Vector(vector),
    "user_id": user_id,
    "created_at": ts,
    "updated_at": ts,
}
```

In the create path:
```python
node_data = {
    "node_type": node_type,
    "content": clean,
    "name_key": name_key,
    "domain": NODE_TYPE_ENTITY,
    "weight": DEFAULT_ENTITY_WEIGHT,
    "metadata": "{}",
    # REMOVED: "entity_links": [],
    "journal_entry_ids": [source_entry_id] if source_entry_id else [],
    "embedding": Vector(vector),
    "user_id": user_id,
    "created_at": ts,
    "updated_at": ts,
}
```

- [ ] **Step 5: Remove `entity_links` and `add_entity_link` from `lethe/graph/ingest.py`**

1. Remove `add_entity_link` from the import at the top:
```python
from lethe.graph.ensure_node import (
    create_relationship_node,
    ensure_node,
    stable_entity_doc_id,
    stable_self_id,
)
```

2. Remove the `"entity_links": []` field from the log entry creation in `run_ingest()`:
```python
await col.document(entry_uuid).set(
    {
        "node_type": NODE_TYPE_LOG,
        "content": text,
        "domain": domain,
        "weight": DEFAULT_LOG_WEIGHT,
        "metadata": "{}",
        "embedding": Vector(vector),
        # REMOVED: "entity_links": [],
        "user_id": user_id,
        "source": source,
        "created_at": ts,
        "updated_at": ts,
    }
)
```

3. Remove the three `add_entity_link` calls from `_process_triple()`:
```python
# DELETE these three lines:
await add_entity_link(db, config, subj_node.uuid, rel_id)
await add_entity_link(db, config, obj_node.uuid, rel_id)
await add_entity_link(db, config, entry_uuid, rel_id)
```

Also remove the `ArrayUnion` import from `ingest.py` if it is now unused (check — it may still be used elsewhere in the file). If unused, remove it from:
```python
from lethe.infra.fs_helpers import ArrayUnion, Vector
```

- [ ] **Step 6: Run all tests**

```bash
./.venv/bin/pytest tests/ -v
```
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add lethe/graph/ensure_node.py lethe/graph/ingest.py
git commit -m "refactor: remove add_entity_link and entity_links field from node writes"
```

---

## Task 6: Traversal Rewrites to Query Relationships Collection

Replace the two-query BFS neighbor lookup with a single `_get_edge_neighbors()` function that queries `relationships`.

**Files:**
- Modify: `lethe/graph/traverse.py`
- Modify: `tests/test_traverse.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_traverse.py`:

```python
@pytest.mark.asyncio
async def test_get_edge_neighbors_queries_relationships_collection():
    from unittest.mock import AsyncMock, MagicMock
    from lethe.graph.traverse import _get_edge_neighbors

    cfg = _config()

    rel_doc = MagicMock()
    rel_doc.id = "rel_abc"
    rel_doc.to_dict.return_value = {
        "subject_uuid": "node-a",
        "predicate": "knows",
        "object_uuid": "node-b",
        "content": "a knows b",
        "weight": 0.8,
        "domain": "general",
        "user_id": "global",
        "journal_entry_ids": [],
    }

    async def fake_stream():
        yield rel_doc

    mock_query = MagicMock()
    mock_query.where.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.stream = fake_stream

    mock_col = MagicMock()
    mock_col.where.return_value = mock_query

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_col

    edges = await _get_edge_neighbors(mock_db, cfg, "node-a", "global")

    mock_db.collection.assert_called_with(cfg.lethe_relationships_collection)
    assert len(edges) >= 1
    assert edges[0].predicate == "knows"
    assert edges[0].uuid == "rel_abc"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/pytest tests/test_traverse.py::test_get_edge_neighbors_queries_relationships_collection -v
```
Expected: `FAILED` — `_get_edge_neighbors` not defined

- [ ] **Step 3: Rewrite `lethe/graph/traverse.py`**

Replace the full file. The key changes: remove `_get_incoming_spo_edges` and `_get_nodes_linking_to`, add `_get_edge_neighbors`, update `_gather_neighbors` return type, rewrite the BFS candidate-collection block to use edges directly.

```python
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from google.cloud import firestore

from lethe.config import Config
from lethe.constants import (
    EMBEDDING_TASK_RETRIEVAL_QUERY,
    NODE_TYPE_LOG,
    TRAVERSAL_OBSERVATION_WEIGHT,
    TRAVERSAL_SIMILARITY_WEIGHT,
    TRAVERSE_BATCH_SIZE,
    TRAVERSE_NEIGHBOR_QUERY_LIMIT,
)
from lethe.graph.ensure_node import doc_to_edge, stable_self_id
from lethe.graph.search import cosine_similarity, doc_to_node
from lethe.infra.embedder import Embedder
from lethe.models.node import Edge, GraphExpandResponse, Node

log = logging.getLogger(__name__)


def apply_self_seed_neighbor_floor(
    pruned: list[Node],
    self_neighbors: list[Node],
    query_vector: Optional[list[float]],
    floor: int,
    hop_idx: int,
    self_in_frontier: bool,
) -> list[Node]:
    """Keep a minimum number of first-hop SELF neighbors from being pruned."""
    if hop_idx != 0 or not self_in_frontier or floor <= 0:
        return pruned

    selected_self = prune_frontier_by_similarity(self_neighbors, query_vector, floor)
    existing = {n.uuid for n in pruned}
    merged = list(pruned)
    for node in selected_self:
        if node.uuid not in existing:
            merged.append(node)
            existing.add(node.uuid)
    return merged


def _is_alive(n: Node) -> bool:
    """False for tombstoned nodes (weight 0.0); True otherwise."""
    return n.weight > 0.0


def prune_frontier_by_similarity(
    nodes: list[Node],
    query_vector: Optional[list[float]],
    top_k: int,
) -> list[Node]:
    if len(nodes) <= top_k:
        return nodes
    max_observation_count = max(len(n.journal_entry_ids) for n in nodes) if nodes else 0
    scored = [
        (
            n,
            (
                (
                    cosine_similarity(n.embedding, query_vector)
                    if (query_vector is not None and n.embedding)
                    else 0.0
                )
                * TRAVERSAL_SIMILARITY_WEIGHT
            )
            + (
                (
                    (len(n.journal_entry_ids) / max_observation_count)
                    if max_observation_count
                    else 0.0
                )
                * TRAVERSAL_OBSERVATION_WEIGHT
            ),
        )
        for n in nodes
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    kept = [n.uuid for n, _ in scored[:top_k]]
    log.info(
        "prune_frontier: candidates=%d top_k=%d query=%s max_obs=%d kept=%s",
        len(nodes),
        top_k,
        bool(query_vector),
        max_observation_count,
        kept,
    )
    return [n for n, _ in scored[:top_k]]


async def _fetch_nodes_by_ids(
    db: firestore.AsyncClient,
    config: Config,
    ids: list[str],
) -> dict[str, Node]:
    col = db.collection(config.lethe_collection)
    result: dict[str, Node] = {}
    for i in range(0, len(ids), TRAVERSE_BATCH_SIZE):
        chunk = ids[i : i + TRAVERSE_BATCH_SIZE]
        refs = [col.document(uid) for uid in chunk]
        async for snap in db.get_all(refs):
            if snap.exists:
                data = snap.to_dict() or {}
                result[snap.id] = doc_to_node(snap.id, data)
    return result


async def _get_edge_neighbors(
    db: firestore.AsyncClient,
    config: Config,
    node_uuid: str,
    user_id: str,
) -> list[Edge]:
    """Return all edges from the relationships collection where node_uuid is subject or object."""
    from lethe.infra.fs_helpers import FieldFilter

    col = db.collection(config.lethe_relationships_collection)

    async def _query_field(field: str) -> list[Edge]:
        q = (
            col.where(filter=FieldFilter(field, "==", node_uuid))
            .where(filter=FieldFilter("user_id", "==", user_id))
            .limit(TRAVERSE_NEIGHBOR_QUERY_LIMIT)
        )
        edges: list[Edge] = []
        try:
            async for doc in q.stream():
                data = doc.to_dict() or {}
                edges.append(doc_to_edge(doc.id, data))
        except Exception as e:
            log.warning("_get_edge_neighbors(%s) failed: %s", field, e)
        return edges

    outgoing, incoming = await asyncio.gather(
        _query_field("subject_uuid"),
        _query_field("object_uuid"),
    )
    seen: set[str] = set()
    result: list[Edge] = []
    for edge in outgoing + incoming:
        if edge.uuid not in seen:
            seen.add(edge.uuid)
            result.append(edge)
    return result


async def graph_expand(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    seed_ids: list[str],
    query: Optional[str],
    hops: int,
    limit_per_edge: int,
    user_id: str,
    self_seed_neighbor_floor: int = 40,
) -> GraphExpandResponse:
    log.info(
        "graph_expand:start seeds=%d hops=%d limit_per_edge=%d user_id=%s has_query=%s",
        len(seed_ids),
        hops,
        limit_per_edge,
        user_id,
        bool(query),
    )
    query_vector: Optional[list[float]] = None
    if query:
        query_vector = await embedder.embed(query, EMBEDDING_TASK_RETRIEVAL_QUERY)

    visited: set[str] = set()
    discovered: set[str] = set()
    all_nodes: dict[str, Node] = {}
    all_edges: list[Edge] = []
    seen_edge_uuids: set[str] = set()

    seed_nodes = await _fetch_nodes_by_ids(db, config, seed_ids)
    visited.update(seed_ids)
    discovered.update(seed_ids)
    for node in seed_nodes.values():
        if node.node_type != NODE_TYPE_LOG and _is_alive(node):
            all_nodes[node.uuid] = node

    frontier = [n for n in seed_nodes.values() if n.node_type != NODE_TYPE_LOG and _is_alive(n)]
    log.info(
        "graph_expand:seed_fetch requested=%d found=%d frontier=%d",
        len(seed_ids),
        len(seed_nodes),
        len(frontier),
    )

    sem = asyncio.Semaphore(10)

    for hop_idx in range(hops):
        if not frontier:
            log.info("graph_expand:hop=%d frontier_empty", hop_idx + 1)
            break

        next_ids: set[str] = set()
        self_neighbor_ids: set[str] = set()
        self_seed_id = stable_self_id(user_id)
        self_in_frontier = any(node.uuid == self_seed_id for node in frontier)

        gather_tasks = [_gather_neighbors(db, config, node, user_id, sem) for node in frontier]
        edge_lists = await asyncio.gather(*gather_tasks)

        for node, edges in zip(frontier, edge_lists):
            for edge in edges:
                if edge.weight > 0.0 and edge.uuid not in seen_edge_uuids:
                    seen_edge_uuids.add(edge.uuid)
                    all_edges.append(edge)
                other = (
                    edge.object_uuid
                    if edge.subject_uuid == node.uuid
                    else edge.subject_uuid
                )
                if other not in discovered:
                    next_ids.add(other)
                    if hop_idx == 0 and node.uuid == self_seed_id:
                        self_neighbor_ids.add(other)

        if not next_ids:
            log.info("graph_expand:hop=%d no_next_ids frontier=%d", hop_idx + 1, len(frontier))
            break
        discovered.update(next_ids)

        candidates = await _fetch_nodes_by_ids(db, config, list(next_ids))
        for n in candidates.values():
            if not _is_alive(n):
                visited.add(n.uuid)
                continue
            if n.node_type == NODE_TYPE_LOG:
                visited.add(n.uuid)
                all_nodes[n.uuid] = n

        non_log = [n for n in candidates.values() if n.node_type != NODE_TYPE_LOG and _is_alive(n)]
        self_neighbors = [n for n in non_log if n.uuid in self_neighbor_ids]

        pruned = prune_frontier_by_similarity(non_log, query_vector, limit_per_edge)
        pruned = apply_self_seed_neighbor_floor(
            pruned=pruned,
            self_neighbors=self_neighbors,
            query_vector=query_vector,
            floor=self_seed_neighbor_floor,
            hop_idx=hop_idx,
            self_in_frontier=self_in_frontier,
        )

        for node in pruned:
            if node.uuid not in visited:
                visited.add(node.uuid)
                all_nodes[node.uuid] = node

        frontier = pruned
        log.info(
            "graph_expand:hop=%d frontier_in=%d next_ids=%d candidates=%d "
            "non_log=%d pruned=%d logs=%d total_nodes=%d total_edges=%d",
            hop_idx + 1,
            len(edge_lists),
            len(next_ids),
            len(candidates),
            len(non_log),
            len(pruned),
            len(candidates) - len(non_log),
            len(all_nodes),
            len(all_edges),
        )

    log.info("graph_expand:done nodes=%d edges=%d", len(all_nodes), len(all_edges))
    return GraphExpandResponse(nodes=all_nodes, edges=all_edges)


async def _gather_neighbors(
    db: firestore.AsyncClient,
    config: Config,
    node: Node,
    user_id: str,
    sem: asyncio.Semaphore,
) -> list[Edge]:
    async with sem:
        return await _get_edge_neighbors(db, config, node.uuid, user_id)
```

- [ ] **Step 4: Run all tests**

```bash
./.venv/bin/pytest tests/ -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lethe/graph/traverse.py tests/test_traverse.py
git commit -m "feat: traversal queries relationships collection via _get_edge_neighbors"
```

---

## Task 7: Remove Dead Fields from Node Model

Strip the SPO fields from `Node` that now belong exclusively to `Edge`. Remove `"relationship"` from `CoreNodeType`.

**Files:**
- Modify: `lethe/models/node.py`
- Modify: `lethe/graph/ensure_node.py`
- Modify: `lethe/graph/ingest.py`
- Modify: `lethe/types.py`
- Modify: `tests/test_node_models.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_node_models.py`:

```python
def test_node_has_no_spo_fields():
    """Confirm SPO fields are no longer on Node — they belong to Edge."""
    n = Node(uuid="x", node_type="entity", content="Alice")
    assert not hasattr(n, "predicate"), "predicate should not be on Node"
    assert not hasattr(n, "subject_uuid"), "subject_uuid should not be on Node"
    assert not hasattr(n, "object_uuid"), "object_uuid should not be on Node"
    assert not hasattr(n, "entity_links"), "entity_links should not be on Node"
    assert not hasattr(n, "relevance_score"), "relevance_score should not be on Node"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/pytest tests/test_node_models.py::test_node_has_no_spo_fields -v
```
Expected: `FAILED` — `Node` still has these attributes

- [ ] **Step 3: Remove fields from `Node` in `lethe/models/node.py`**

Remove these fields from the `Node` class:

```python
# DELETE these lines:
entity_links: list[str] = Field(default_factory=list)
predicate: Optional[str] = None
object_uuid: Optional[str] = None
subject_uuid: Optional[str] = None
relevance_score: Optional[float] = None
```

- [ ] **Step 4: Update `doc_to_node()` in `lethe/graph/ensure_node.py`**

Remove the removed fields from `doc_to_node()`:

```python
def doc_to_node(doc_id: str, data: dict) -> Node:
    data.pop("vector_distance", None)
    embedding = None
    raw_emb = data.get("embedding")
    if raw_emb is not None:
        try:
            embedding = list(raw_emb)
        except TypeError:
            embedding = None
    return Node(
        uuid=doc_id,
        node_type=data.get("node_type", DEFAULT_NODE_TYPE),
        content=data.get("content", ""),
        domain=data.get("domain", DEFAULT_DOMAIN),
        weight=float(data.get("weight", data.get("significance_weight", 0.5))),
        metadata=data.get("metadata", "{}"),
        journal_entry_ids=list(data.get("journal_entry_ids", [])),
        name_key=data.get("name_key"),
        user_id=data.get("user_id", DEFAULT_USER_ID),
        source=data.get("source"),
        created_at=parse_to_utc(data.get("created_at")),
        updated_at=parse_to_utc(data.get("updated_at")),
        embedding=embedding,
    )
```

- [ ] **Step 5: Update `_get_or_create_entity_node()` in `lethe/graph/ingest.py`**

Remove the deleted fields from the manual `Node(...)` construction inside `_get_or_create_entity_node()`:

```python
node = Node(
    uuid=existing_uuid,
    node_type=data.get("node_type", fallback_type),
    content=(data.get("content") or resolved_term["text"]),
    domain=data.get("domain", "entity"),
    weight=float(
        data.get("weight", data.get("significance_weight", DEFAULT_ENTITY_WEIGHT))
    ),
    metadata=data.get("metadata", "{}"),
    journal_entry_ids=list(data.get("journal_entry_ids", [])),
    name_key=data.get("name_key"),
    user_id=data.get("user_id", user_id),
    source=data.get("source"),
)
```

- [ ] **Step 6: Remove `"relationship"` from `CoreNodeType` in `lethe/types.py`**

```python
CoreNodeType: TypeAlias = Literal[
    "log",
    "entity",
]
```

- [ ] **Step 7: Update `tests/test_node_models.py`**

Remove the `assert n.entity_links == []` line from `test_node_defaults()`:

```python
def test_node_defaults():
    n = Node(uuid="abc", node_type="generic", content="hello")
    assert n.user_id == "global"
    assert n.metadata == "{}"
    assert n.weight == 0.5
    assert n.domain == "general"
```

- [ ] **Step 8: Run all tests**

```bash
./.venv/bin/pytest tests/ -v
```
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add lethe/models/node.py lethe/graph/ensure_node.py lethe/graph/ingest.py \
        lethe/types.py tests/test_node_models.py
git commit -m "refactor: remove SPO fields from Node model, retire relationship node_type"
```

---

## Task 8: Firestore Indexes

Add required indexes for the new `relationships` collection. Remove the two `nodes` indexes that existed only to support the old traversal pattern.

**Files:**
- Modify: `firestore.indexes.json`

- [ ] **Step 1: Update `firestore.indexes.json`**

Remove these two index entries from the `nodes` collection (they exist only for the old `entity_links` and `object_uuid` traversal queries):

```json
// DELETE — was used by _get_nodes_linking_to (entity_links ARRAY_CONTAINS query):
{
  "collectionGroup": "nodes",
  "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "entity_links", "arrayConfig": "CONTAINS" },
    { "fieldPath": "user_id", "order": "ASCENDING" }
  ]
},
// DELETE — was used by _get_incoming_spo_edges (object_uuid == node_uuid query):
{
  "collectionGroup": "nodes",
  "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "object_uuid", "order": "ASCENDING" },
    { "fieldPath": "user_id", "order": "ASCENDING" }
  ]
},
// DELETE — was used by create_relationship_node (subject_uuid + node_type + updated_at query):
{
  "collectionGroup": "nodes",
  "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "user_id", "order": "ASCENDING" },
    { "fieldPath": "subject_uuid", "order": "ASCENDING" },
    { "fieldPath": "node_type", "order": "ASCENDING" },
    { "fieldPath": "updated_at", "order": "DESCENDING" }
  ]
}
```

Add these new index entries for the `relationships` collection:

```json
{
  "collectionGroup": "relationships",
  "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "user_id", "order": "ASCENDING" },
    { "fieldPath": "subject_uuid", "order": "ASCENDING" }
  ]
},
{
  "collectionGroup": "relationships",
  "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "user_id", "order": "ASCENDING" },
    { "fieldPath": "object_uuid", "order": "ASCENDING" }
  ]
},
{
  "collectionGroup": "relationships",
  "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "user_id", "order": "ASCENDING" },
    {
      "fieldPath": "embedding",
      "vectorConfig": { "dimension": 768, "flat": {} }
    }
  ]
},
{
  "collectionGroup": "relationships",
  "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "user_id", "order": "ASCENDING" },
    { "fieldPath": "domain", "order": "ASCENDING" },
    {
      "fieldPath": "embedding",
      "vectorConfig": { "dimension": 768, "flat": {} }
    }
  ]
},
{
  "collectionGroup": "relationships",
  "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "user_id", "order": "ASCENDING" },
    { "fieldPath": "subject_uuid", "order": "ASCENDING" },
    { "fieldPath": "updated_at", "order": "DESCENDING" }
  ]
}
```

Also add a `fieldOverrides` entry for `relationships.embedding` (same pattern as `nodes.embedding`):

```json
{
  "collectionGroup": "relationships",
  "fieldPath": "embedding",
  "indexes": [],
  "vectorConfig": {
    "dimension": 768,
    "flat": {}
  }
}
```

- [ ] **Step 2: Run all tests to confirm nothing broke**

```bash
./.venv/bin/pytest tests/ -v
```
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add firestore.indexes.json
git commit -m "feat: add relationships collection indexes, remove stale nodes indexes"
```

---

## Self-Review Checklist

Before handing to executor, verify:

- [ ] All spec sections have a corresponding task: data model ✓, search ✓, traversal ✓, ingest ✓, indexes ✓
- [ ] No "TBD" or placeholder steps
- [ ] `Edge` field names are consistent across all tasks: `subject_uuid`, `object_uuid`, `uuid` throughout (not `subject`/`object`)
- [ ] `doc_to_edge` defined in Task 2, used in Tasks 3 and 6 — import path `lethe.graph.ensure_node` throughout
- [ ] `config.lethe_relationships_collection` defined in Task 1, used in Tasks 3, 4, 6 — consistent
- [ ] `execute_search` return type changes to `tuple[list[Node], list[Edge]]` in Task 3 — both callers updated (routers/search.py and routers/graph.py)
- [ ] `test_routers.py` search mocks updated in Task 3 — all six test functions listed
- [ ] `Node` field removals in Task 7 covered in `doc_to_node`, `_get_or_create_entity_node`, and test assertions
