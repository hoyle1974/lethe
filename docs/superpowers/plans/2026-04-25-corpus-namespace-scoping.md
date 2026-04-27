# Corpus Namespace Scoping (Plan B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `source_filter` to `GraphExpandRequest` so BFS traversal can be scoped to a single corpus — excluding nodes from other corpora while preserving shared entity nodes.

**Architecture:** Add `source_filter: str | None` to the request model and `graph_expand` function. After fetching candidates in each BFS hop, apply `_passes_source_filter(node, source_filter)`: nodes with `source=None` (shared entities) always pass; nodes with `source != source_filter` are excluded. Wire the parameter through the two call sites in the router (expand + summarize).

**Tech Stack:** Pure Python logic on existing Firestore + BFS stack — no new dependencies.

---

## Data Model Note

Nodes with `source` set are: `document` nodes, `chunk` nodes, `log`/summary nodes — all tagged `source=corpus_id` during corpus ingestion. Entity nodes created via `ensure_node` have `source=None` (they are shared across corpora by design). The `source_filter` therefore scopes corpus-specific storage nodes while keeping shared entities reachable. Full entity-level isolation would require source propagation into `ensure_node`, which is out of scope here.

---

## File Map

**Modify:**
- `lethe/models/node.py:82-89` — add `source_filter: str | None = None` to `GraphExpandRequest`
- `lethe/graph/traverse.py` — add `_passes_source_filter(node, source_filter)` helper; add `source_filter` param to `graph_expand`; apply filter to seed frontier and BFS candidates
- `lethe/routers/graph.py:97-114, 146-156, 268-278` — pass `req.source_filter` in all three `graph_expand` calls
- `tests/test_traverse.py` — add four unit tests for `_passes_source_filter` and one integration test via the router
- `wiki/api.md` — document `source_filter` field on `POST /v1/graph/expand` and `POST /v1/graph/summarize`
- `wiki/algorithms.md` — update §5 BFS with source filter note
- `wiki/log.md` — append entry

---

## Task 1: TDD — `source_filter` on `GraphExpandRequest` and `_passes_source_filter`

**Files:**
- Modify: `tests/test_traverse.py`
- Modify: `lethe/models/node.py`
- Modify: `lethe/graph/traverse.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_traverse.py`:

```python
def test_passes_source_filter_none_allows_all():
    from lethe.graph.traverse import _passes_source_filter

    node_with_source = Node(uuid="a", node_type="chunk", content="x", source="corpus-A")
    node_no_source = Node(uuid="b", node_type="person", content="Alice")
    assert _passes_source_filter(node_with_source, None) is True
    assert _passes_source_filter(node_no_source, None) is True


def test_passes_source_filter_entity_node_always_passes():
    from lethe.graph.traverse import _passes_source_filter

    entity = Node(uuid="e", node_type="person", content="Alice")  # source defaults to None
    assert _passes_source_filter(entity, "corpus-A") is True


def test_passes_source_filter_matching_source_passes():
    from lethe.graph.traverse import _passes_source_filter

    node = Node(uuid="c", node_type="chunk", content="x", source="corpus-A")
    assert _passes_source_filter(node, "corpus-A") is True


def test_passes_source_filter_non_matching_source_excluded():
    from lethe.graph.traverse import _passes_source_filter

    node = Node(uuid="d", node_type="chunk", content="x", source="corpus-B")
    assert _passes_source_filter(node, "corpus-A") is False


def test_graph_expand_request_accepts_source_filter():
    from lethe.models.node import GraphExpandRequest

    req = GraphExpandRequest(seed_ids=["abc"], source_filter="corpus-123")
    assert req.source_filter == "corpus-123"


def test_graph_expand_request_source_filter_defaults_none():
    from lethe.models.node import GraphExpandRequest

    req = GraphExpandRequest(seed_ids=["abc"])
    assert req.source_filter is None
```

- [ ] **Step 2: Run to confirm failure**

Run: `./.venv/bin/pytest tests/test_traverse.py::test_passes_source_filter_none_allows_all -v`
Expected: FAIL — `ImportError: cannot import name '_passes_source_filter'`

- [ ] **Step 3: Add `source_filter` to `GraphExpandRequest`**

In `lethe/models/node.py`, update `GraphExpandRequest`:

```python
class GraphExpandRequest(BaseModel):
    seed_ids: list[str]
    query: str | None = Field(default=None, max_length=500)
    hops: int = 2
    limit_per_edge: int = 20
    self_seed_neighbor_floor: int = 40
    debug: bool = False
    user_id: str = DEFAULT_USER_ID
    source_filter: str | None = None
```

- [ ] **Step 4: Add `_passes_source_filter` to `lethe/graph/traverse.py`**

Add this function after `_is_alive`:

```python
def _passes_source_filter(node: Node, source_filter: Optional[str]) -> bool:
    """True if node should be included given source_filter.

    Nodes with source=None (shared entities) always pass.
    Nodes with a source tag only pass if it matches source_filter.
    """
    if source_filter is None:
        return True
    return node.source is None or node.source == source_filter
```

- [ ] **Step 5: Run all six new tests**

Run: `./.venv/bin/pytest tests/test_traverse.py -k "source_filter" -v`
Expected: All 6 PASS

- [ ] **Step 6: Lint**

Run: `./.venv/bin/ruff format lethe/models/node.py lethe/graph/traverse.py tests/test_traverse.py && ./.venv/bin/ruff check --fix lethe/models/node.py lethe/graph/traverse.py tests/test_traverse.py`

- [ ] **Step 7: Commit**

```bash
git add lethe/models/node.py lethe/graph/traverse.py tests/test_traverse.py
git commit -m "feat: add source_filter to GraphExpandRequest and _passes_source_filter to traverse.py"
```

---

## Task 2: Wire `source_filter` into `graph_expand` and the router

**Files:**
- Modify: `lethe/graph/traverse.py`
- Modify: `lethe/routers/graph.py`
- Modify: `tests/test_traverse.py`

- [ ] **Step 1: Write the failing router test**

Append to `tests/test_traverse.py`:

```python
def test_graph_expand_excludes_non_matching_source_nodes(mock_embedder, mock_llm):
    """When source_filter is set, nodes whose source != source_filter are excluded from results."""
    import os
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi.testclient import TestClient

    from lethe.graph.canonical_map import CanonicalMap

    def _make_client(mock_db):
        from lethe.config import Config
        from lethe.deps import get_canonical_map
        from lethe.main import app

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-proj"}, clear=True):
            cfg = Config()
        app.state.config = cfg
        app.state.db = mock_db
        app.state.embedder = mock_embedder
        app.state.llm = mock_llm
        app.dependency_overrides[get_canonical_map] = lambda: CanonicalMap()
        return TestClient(app, raise_server_exceptions=True)

    # Seed node: entity with no source (shared)
    seed_snap = MagicMock()
    seed_snap.exists = True
    seed_snap.id = "seed-entity-1"
    seed_snap.to_dict.return_value = {
        "node_type": "person",
        "content": "Alice",
        "domain": "general",
        "weight": 0.55,
        "metadata": "{}",
        "journal_entry_ids": [],
        "user_id": "global",
        "source": None,
    }

    # Neighbour from corpus-A: should pass filter
    corpus_a_snap = MagicMock()
    corpus_a_snap.exists = True
    corpus_a_snap.id = "chunk-corpus-a"
    corpus_a_snap.to_dict.return_value = {
        "node_type": "chunk",
        "content": "Alice's notes from corpus A",
        "domain": "general",
        "weight": 0.4,
        "metadata": "{}",
        "journal_entry_ids": [],
        "user_id": "global",
        "source": "corpus-A",
    }

    # Neighbour from corpus-B: should be excluded
    corpus_b_snap = MagicMock()
    corpus_b_snap.exists = True
    corpus_b_snap.id = "chunk-corpus-b"
    corpus_b_snap.to_dict.return_value = {
        "node_type": "chunk",
        "content": "Alice's notes from corpus B",
        "domain": "general",
        "weight": 0.4,
        "metadata": "{}",
        "journal_entry_ids": [],
        "user_id": "global",
        "source": "corpus-B",
    }

    # Edge connecting seed to both corpus nodes
    edge_snap_a = MagicMock()
    edge_snap_a.id = "rel-a"
    edge_snap_a.to_dict.return_value = {
        "subject_uuid": "seed-entity-1",
        "predicate": "references",
        "object_uuid": "chunk-corpus-a",
        "content": "Alice references corpus-A chunk",
        "weight": 0.8,
        "domain": "general",
        "user_id": "global",
        "journal_entry_ids": [],
    }
    edge_snap_b = MagicMock()
    edge_snap_b.id = "rel-b"
    edge_snap_b.to_dict.return_value = {
        "subject_uuid": "seed-entity-1",
        "predicate": "references",
        "object_uuid": "chunk-corpus-b",
        "content": "Alice references corpus-B chunk",
        "weight": 0.8,
        "domain": "general",
        "user_id": "global",
        "journal_entry_ids": [],
    }

    async def fake_get_all(refs):
        id_map = {
            "seed-entity-1": seed_snap,
            "chunk-corpus-a": corpus_a_snap,
            "chunk-corpus-b": corpus_b_snap,
        }
        for ref in refs:
            snap = id_map.get(ref.id)
            if snap:
                yield snap

    async def fake_stream_edges_subject():
        yield edge_snap_a
        yield edge_snap_b

    async def fake_stream_edges_object():
        return
        yield  # empty async generator

    mock_query_subj = MagicMock()
    mock_query_subj.where.return_value = mock_query_subj
    mock_query_subj.limit.return_value = mock_query_subj
    mock_query_subj.stream = fake_stream_edges_subject

    mock_query_obj = MagicMock()
    mock_query_obj.where.return_value = mock_query_obj
    mock_query_obj.limit.return_value = mock_query_obj
    mock_query_obj.stream = fake_stream_edges_object

    call_count = 0

    def where_side_effect(filter):
        nonlocal call_count
        call_count += 1
        # Alternate between subject and object queries
        if call_count % 2 == 1:
            return mock_query_subj
        return mock_query_obj

    mock_rel_col = MagicMock()
    mock_rel_col.where.side_effect = where_side_effect

    def _make_col(name):
        if "relationship" in name:
            return mock_rel_col
        col = MagicMock()

        async def fake_get_all_inner(refs):
            id_map = {
                "seed-entity-1": seed_snap,
                "chunk-corpus-a": corpus_a_snap,
                "chunk-corpus-b": corpus_b_snap,
            }
            for ref in refs:
                snap = id_map.get(ref.id)
                if snap:
                    yield snap

        return col

    mock_db = MagicMock()
    mock_db.get_all = fake_get_all
    mock_db.collection.side_effect = _make_col

    client = _make_client(mock_db)
    resp = client.post(
        "/v1/graph/expand",
        json={
            "seed_ids": ["seed-entity-1"],
            "hops": 1,
            "source_filter": "corpus-A",
            "user_id": "global",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    node_ids = list(data["nodes"].keys())
    assert "chunk-corpus-b" not in node_ids, "corpus-B node should be excluded"
```

- [ ] **Step 2: Run to confirm failure**

Run: `./.venv/bin/pytest "tests/test_traverse.py::test_graph_expand_excludes_non_matching_source_nodes" -v`
Expected: FAIL — `graph_expand` doesn't accept `source_filter` yet, or the node appears when it shouldn't

- [ ] **Step 3: Add `source_filter` parameter to `graph_expand` in `lethe/graph/traverse.py`**

Update the `graph_expand` function signature (line ~152):

```python
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
    source_filter: Optional[str] = None,
) -> GraphExpandResponse:
```

In the body, update the seed frontier filtering (after `seed_nodes = await _fetch_nodes_by_ids(...)`):

```python
    frontier = [
        n
        for n in seed_nodes.values()
        if n.node_type != NODE_TYPE_LOG and _is_alive(n) and _passes_source_filter(n, source_filter)
    ]
```

In the BFS hop loop, after `non_log = [n for n in candidates.values() if n.node_type != NODE_TYPE_LOG and _is_alive(n)]`, add:

```python
        if source_filter:
            non_log = [n for n in non_log if _passes_source_filter(n, source_filter)]
```

- [ ] **Step 4: Wire `source_filter` through the router in `lethe/routers/graph.py`**

In the `expand` endpoint (3 lines changed):

```python
@router.post("/v1/graph/expand", response_model=GraphExpandResponse)
async def expand(
    req: GraphExpandRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    config: Config = Depends(get_config),
) -> GraphExpandResponse:
    return await graph_expand(
        db=db,
        embedder=embedder,
        config=config,
        seed_ids=req.seed_ids,
        query=req.query,
        hops=req.hops,
        limit_per_edge=req.limit_per_edge,
        self_seed_neighbor_floor=req.self_seed_neighbor_floor,
        user_id=req.user_id,
        source_filter=req.source_filter,
    )
```

In the `summarize` endpoint, the first `graph_expand` call (pass 1, around line 146):

```python
    expanded = await graph_expand(
        db=db,
        embedder=embedder,
        config=config,
        seed_ids=req.seed_ids,
        query=expansion_query,
        hops=req.hops,
        limit_per_edge=req.limit_per_edge,
        self_seed_neighbor_floor=req.self_seed_neighbor_floor,
        user_id=req.user_id,
        source_filter=req.source_filter,
    )
```

In the `summarize` endpoint, the second `graph_expand` call (pass 2, around line 268):

```python
        extra = await graph_expand(
            db=db,
            embedder=embedder,
            config=config,
            seed_ids=retrieval_seed_ids,
            query=expansion_query,
            hops=1,
            limit_per_edge=req.limit_per_edge,
            self_seed_neighbor_floor=req.self_seed_neighbor_floor,
            user_id=req.user_id,
            source_filter=req.source_filter,
        )
```

- [ ] **Step 5: Run all traverse tests**

Run: `./.venv/bin/pytest tests/test_traverse.py -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite**

Run: `./.venv/bin/pytest -v`
Expected: All 190+ tests PASS

- [ ] **Step 7: Lint**

Run: `./.venv/bin/ruff format lethe/graph/traverse.py lethe/routers/graph.py tests/test_traverse.py && ./.venv/bin/ruff check --fix lethe/graph/traverse.py lethe/routers/graph.py tests/test_traverse.py`

- [ ] **Step 8: Commit**

```bash
git add lethe/graph/traverse.py lethe/routers/graph.py tests/test_traverse.py
git commit -m "feat: add source_filter to graph_expand — BFS excludes nodes from non-matching corpora"
```

---

## Task 3: Update wiki docs

**Files:**
- Modify: `wiki/api.md`
- Modify: `wiki/algorithms.md`
- Modify: `wiki/log.md`

- [ ] **Step 1: Update `wiki/api.md` — add `source_filter` to both graph endpoints**

In the `POST /v1/graph/expand` table, add a new row after `user_id`:

```markdown
| `source_filter` | string | no | null | If set, exclude BFS candidates whose `source` != this value (nodes with `source=null` always pass) |
```

In the `POST /v1/graph/summarize` description, add after "Uses same request schema":
```
Includes `source_filter` from the request on both BFS passes.
```

- [ ] **Step 2: Update `wiki/algorithms.md` §5 BFS**

Append this block at the end of section 5 (after the Tombstone exclusion line):

```markdown
**Source filter**: When `source_filter` is set, candidates are pre-filtered before `prune_frontier_by_similarity`. A candidate passes if `node.source is None` (shared entity) or `node.source == source_filter`. Seed nodes are filtered the same way. Use this to scope expansion to a single corpus while keeping shared entity nodes reachable.
```

- [ ] **Step 3: Append to `wiki/log.md`**

```
2026-04-25: [algorithms] §5 BFS updated — source_filter pre-filtering for namespace scoping
2026-04-25: [api.md] Added source_filter field to POST /v1/graph/expand and /v1/graph/summarize
```

- [ ] **Step 4: Commit**

```bash
git add wiki/api.md wiki/algorithms.md wiki/log.md
git commit -m "docs: document source_filter on graph expand/summarize endpoints and BFS algorithm"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|-----------------|------|
| `GraphExpandRequest` accepts a scoping parameter | Task 1 (source_filter field) |
| BFS does not traverse into nodes from other corpora | Task 2 (graph_expand filter) |
| Shared entities remain reachable across corpora | Task 2 (_passes_source_filter — source=None always passes) |
| Both expand and summarize endpoints respect scope | Task 2 (all 3 graph_expand call sites wired) |
| Wiki updated | Task 3 |

**Placeholder scan:** None found.

**Type consistency:**
- `source_filter: str | None = None` on `GraphExpandRequest` ✓
- `_passes_source_filter(node: Node, source_filter: Optional[str]) -> bool` ✓
- `graph_expand(..., source_filter: Optional[str] = None)` ✓
- Router passes `req.source_filter` (str | None) to `graph_expand` ✓
