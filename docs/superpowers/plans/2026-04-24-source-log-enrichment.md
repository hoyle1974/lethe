# Source Log Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich the summarization pipeline's LLM context with the original source text that produced each entity node, so the LLM sees both `[structured fact] Jack works_on Lethe` and `[source] "Started building Lethe last month..."` for every entity in the graph.

**Architecture:** After graph BFS expansion, fetch the most recent log nodes referenced by each entity's `journal_entry_ids` in a separate Firestore batch. Pass the result to `to_markdown()` as an optional `source_logs` dict. Only the final summarization pass (pass 2 / combined graph) gets source enrichment — the draft pass is intermediate and not user-visible.

**Tech Stack:** Python 3.14, FastAPI, google-cloud-firestore (async), Pydantic, pytest + pytest-asyncio

---

## File Map

| Action | File | What changes |
|--------|------|-------------|
| Modify | `lethe/constants.py` | Add `SOURCE_LOGS_MAX_PER_NODE`, `SOURCE_LOGS_MAX_TOTAL`, `SOURCE_LOG_SNIPPET_LENGTH` |
| Create | `lethe/graph/source_fetch.py` | New `fetch_source_logs()` async function |
| Create | `tests/test_source_fetch.py` | Tests for `fetch_source_logs()` |
| Modify | `lethe/models/node.py` | Update `GraphExpandResponse.to_markdown()` signature + body |
| Modify | `tests/test_node_models.py` | Update existing snippet test + add source_logs test |
| Modify | `lethe/routers/graph.py` | Call `fetch_source_logs()` before `final_md`, pass to `to_markdown()` |
| Modify | `wiki/algorithms.md` | Document source enrichment step |
| Modify | `wiki/log.md` | Append change entry |

---

### Task 1: Add constants

**Files:**
- Modify: `lethe/constants.py`

- [ ] **Step 1: Add three source-log constants to `lethe/constants.py` after the `TRAVERSE_BATCH_SIZE` line**

```python
# Source log enrichment limits
SOURCE_LOGS_MAX_PER_NODE = 2
SOURCE_LOGS_MAX_TOTAL = 30
SOURCE_LOG_SNIPPET_LENGTH = 250
```

Final state of the bottom of `lethe/constants.py`:
```python
# Firestore/query limits and batching
CONSOLIDATION_LOG_QUERY_LIMIT = 50
RELATIONSHIP_SUPERSEDE_CANDIDATE_LIMIT = 10
TRAVERSE_NEIGHBOR_QUERY_LIMIT = 50
TRAVERSE_BATCH_SIZE = 100

# Source log enrichment limits
SOURCE_LOGS_MAX_PER_NODE = 2
SOURCE_LOGS_MAX_TOTAL = 30
SOURCE_LOG_SNIPPET_LENGTH = 250
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `./.venv/bin/python -c "from lethe.constants import SOURCE_LOGS_MAX_PER_NODE, SOURCE_LOGS_MAX_TOTAL, SOURCE_LOG_SNIPPET_LENGTH; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add lethe/constants.py
git commit -m "feat: add source log enrichment constants"
```

---

### Task 2: Implement `fetch_source_logs` (TDD)

**Files:**
- Create: `lethe/graph/source_fetch.py`
- Create: `tests/test_source_fetch.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_source_fetch.py`:

```python
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from lethe.models.node import Node


def _config():
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test"}, clear=True):
        from lethe.config import Config
        return Config(_env_file=None)


def _entity(uuid: str, journal_ids: list[str]) -> Node:
    return Node(uuid=uuid, node_type="person", content=uuid, journal_entry_ids=journal_ids)


def _log(uuid: str, content: str) -> Node:
    return Node(uuid=uuid, node_type="log", content=content)


@pytest.mark.asyncio
async def test_fetch_source_logs_returns_log_nodes_for_entities():
    from lethe.graph.source_fetch import fetch_source_logs

    cfg = _config()
    entity_nodes = {
        "ent-1": _entity("ent-1", ["log-a", "log-b"]),
    }

    log_snap_a = MagicMock()
    log_snap_a.exists = True
    log_snap_a.id = "log-a"
    log_snap_a.to_dict.return_value = {
        "node_type": "log",
        "content": "First entry",
        "domain": "general",
        "weight": 0.3,
        "metadata": "{}",
        "journal_entry_ids": [],
        "user_id": "global",
    }

    log_snap_b = MagicMock()
    log_snap_b.exists = True
    log_snap_b.id = "log-b"
    log_snap_b.to_dict.return_value = {
        "node_type": "log",
        "content": "Second entry",
        "domain": "general",
        "weight": 0.3,
        "metadata": "{}",
        "journal_entry_ids": [],
        "user_id": "global",
    }

    async def _fake_get_all(refs):
        yield log_snap_a
        yield log_snap_b

    mock_db = MagicMock()
    mock_db.get_all = _fake_get_all
    mock_db.collection.return_value.document.return_value = MagicMock()

    result = await fetch_source_logs(entity_nodes, mock_db, cfg)

    assert "ent-1" in result
    contents = [n.content for n in result["ent-1"]]
    assert "First entry" in contents or "Second entry" in contents


@pytest.mark.asyncio
async def test_fetch_source_logs_respects_max_per_node():
    from lethe.graph.source_fetch import fetch_source_logs

    cfg = _config()
    # Entity has 5 journal entries — should only fetch last max_per_node=2
    entity_nodes = {
        "ent-1": _entity("ent-1", ["log-1", "log-2", "log-3", "log-4", "log-5"]),
    }

    fetched_ids: list[str] = []

    async def _fake_get_all(refs):
        for ref in refs:
            fetched_ids.append(ref.id)
        return
        yield  # make it an async generator

    mock_db = MagicMock()
    mock_db.get_all = _fake_get_all
    mock_db.collection.return_value.document = lambda uid: MagicMock(id=uid)

    await fetch_source_logs(entity_nodes, mock_db, cfg, max_per_node=2)

    # Should only request the 2 most recent IDs (last 2 in the list)
    assert set(fetched_ids) <= {"log-4", "log-5"}


@pytest.mark.asyncio
async def test_fetch_source_logs_skips_log_entity_nodes():
    """Log nodes in entity_nodes dict should not be processed."""
    from lethe.graph.source_fetch import fetch_source_logs

    cfg = _config()
    entity_nodes = {
        "log-node": Node(
            uuid="log-node",
            node_type="log",
            content="raw entry",
            journal_entry_ids=["should-not-fetch"],
        ),
    }

    async def _fake_get_all(refs):
        assert False, "should not have been called"
        yield

    mock_db = MagicMock()
    mock_db.get_all = _fake_get_all

    result = await fetch_source_logs(entity_nodes, mock_db, cfg)
    assert result == {}


@pytest.mark.asyncio
async def test_fetch_source_logs_empty_nodes():
    from lethe.graph.source_fetch import fetch_source_logs

    cfg = _config()

    async def _fake_get_all(refs):
        return
        yield

    mock_db = MagicMock()
    mock_db.get_all = _fake_get_all

    result = await fetch_source_logs({}, mock_db, cfg)
    assert result == {}
```

- [ ] **Step 2: Run to confirm all 4 tests fail with ImportError**

Run: `./.venv/bin/pytest tests/test_source_fetch.py -v`
Expected: 4 errors — `ModuleNotFoundError: No module named 'lethe.graph.source_fetch'`

- [ ] **Step 3: Create `lethe/graph/source_fetch.py`**

```python
from __future__ import annotations

import logging

from google.cloud import firestore

from lethe.config import Config
from lethe.constants import (
    NODE_TYPE_LOG,
    SOURCE_LOGS_MAX_PER_NODE,
    SOURCE_LOGS_MAX_TOTAL,
    TRAVERSE_BATCH_SIZE,
)
from lethe.graph.serialization import doc_to_node
from lethe.models.node import Node

log = logging.getLogger(__name__)


async def fetch_source_logs(
    entity_nodes: dict[str, Node],
    db: firestore.AsyncClient,
    config: Config,
    max_per_node: int = SOURCE_LOGS_MAX_PER_NODE,
    max_total: int = SOURCE_LOGS_MAX_TOTAL,
) -> dict[str, list[Node]]:
    """Fetch the most recent log nodes for each entity node by journal_entry_ids.

    Returns entity_uuid -> [log_node, ...], capped at max_per_node per entity
    and max_total log fetches total.
    """
    per_entity: dict[str, list[str]] = {}
    for uuid, node in entity_nodes.items():
        if node.node_type != NODE_TYPE_LOG and node.journal_entry_ids:
            per_entity[uuid] = node.journal_entry_ids[-max_per_node:]

    if not per_entity:
        return {}

    id_to_entities: dict[str, list[str]] = {}
    for entity_uuid, log_ids in per_entity.items():
        for log_id in log_ids:
            id_to_entities.setdefault(log_id, []).append(entity_uuid)

    fetch_ids = list(id_to_entities.keys())[:max_total]

    col = db.collection(config.lethe_collection)
    fetched: dict[str, Node] = {}
    for i in range(0, len(fetch_ids), TRAVERSE_BATCH_SIZE):
        chunk = fetch_ids[i : i + TRAVERSE_BATCH_SIZE]
        refs = [col.document(uid) for uid in chunk]
        async for snap in db.get_all(refs):
            if snap.exists:
                data = snap.to_dict() or {}
                node = doc_to_node(snap.id, data)
                if node.node_type == NODE_TYPE_LOG:
                    fetched[snap.id] = node

    log.info(
        "fetch_source_logs: fetched=%d log_nodes for %d entities",
        len(fetched),
        len(per_entity),
    )

    result: dict[str, list[Node]] = {}
    for entity_uuid, log_ids in per_entity.items():
        logs_for_entity = [fetched[lid] for lid in log_ids if lid in fetched]
        if logs_for_entity:
            result[entity_uuid] = logs_for_entity

    return result
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `./.venv/bin/pytest tests/test_source_fetch.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Lint**

Run: `./.venv/bin/ruff format . && ./.venv/bin/ruff check --fix .`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add lethe/graph/source_fetch.py tests/test_source_fetch.py
git commit -m "feat: add fetch_source_logs to retrieve source log nodes for entity nodes"
```

---

### Task 3: Update `to_markdown()` to accept and render source logs

**Files:**
- Modify: `lethe/models/node.py`
- Modify: `tests/test_node_models.py`

- [ ] **Step 1: Update the existing snippet test and add a new source_logs test in `tests/test_node_models.py`**

Replace the existing `test_graph_expand_to_markdown_includes_metadata_and_recent_log_snippet` test and add a new one. Find the test at line 85 and replace through line 113:

```python
def test_graph_expand_to_markdown_includes_log_snippet_from_self_nodes():
    """Falls back to log nodes already in self.nodes when no source_logs provided."""
    r = GraphExpandResponse(
        nodes={
            "entity": Node(
                uuid="entity",
                node_type="person",
                content="Alice",
                metadata='{"role":"engineer"}',
                journal_entry_ids=["log-old", "log-new"],
            ),
            "log-old": Node(uuid="log-old", node_type="log", content="Old note"),
            "log-new": Node(
                uuid="log-new",
                node_type="log",
                content="Recent note " + ("x" * 300),
            ),
        },
        edges=[],
    )
    md = r.to_markdown(seed_ids=["entity"])
    assert 'metadata={"role":"engineer"}' in md
    assert '[source]' in md
    source_line = next(line for line in md.splitlines() if "[source]" in line)
    # snippet is inside quotes; extract between first and last "
    snippet = source_line.split('"', 1)[1].rsplit('"', 1)[0]
    assert len(snippet) <= 250


def test_graph_expand_to_markdown_uses_source_logs_when_provided():
    """source_logs parameter takes priority over log nodes in self.nodes."""
    entity = Node(
        uuid="ent-1",
        node_type="person",
        content="Jack",
        journal_entry_ids=["log-1"],
    )
    log_node = Node(
        uuid="log-1",
        node_type="log",
        content="Jack started working on Lethe last month",
    )
    r = GraphExpandResponse(
        nodes={"ent-1": entity},  # log-1 NOT in self.nodes
        edges=[],
    )
    source_logs = {"ent-1": [log_node]}
    md = r.to_markdown(seed_ids=["ent-1"], source_logs=source_logs)
    assert "Jack started working on Lethe last month" in md
    assert '[source]' in md


def test_graph_expand_to_markdown_no_source_logs_no_snippet():
    """When no source_logs provided and no log nodes in self.nodes, no [source] line."""
    r = GraphExpandResponse(
        nodes={"ent-1": Node(uuid="ent-1", node_type="person", content="Alice")},
        edges=[],
    )
    md = r.to_markdown(seed_ids=["ent-1"])
    assert "[source]" not in md
```

- [ ] **Step 2: Run the three updated/new tests to confirm they fail**

Run: `./.venv/bin/pytest tests/test_node_models.py -k "snippet or source_logs or no_snippet" -v`
Expected: the two new tests FAIL (function signature doesn't accept `source_logs`), the renamed fallback test FAIL (format changed)

- [ ] **Step 3: Update `GraphExpandResponse.to_markdown()` in `lethe/models/node.py`**

First add `SOURCE_LOG_SNIPPET_LENGTH` to the imports at the top of the file:

```python
from lethe.constants import DEFAULT_DOMAIN, DEFAULT_RELATIONSHIP_WEIGHT, DEFAULT_USER_ID, SOURCE_LOG_SNIPPET_LENGTH
```

Then replace the entire `to_markdown` method (lines 90–111):

```python
    def to_markdown(
        self,
        seed_ids: list[str],
        source_logs: dict[str, list[Node]] | None = None,
    ) -> str:
        lines = ["## Knowledge Graph\n"]
        for uuid, node in self.nodes.items():
            if node.node_type == "log":
                continue
            marker = " [SEED]" if uuid in seed_ids else ""
            lines.append(
                f"- **{node.node_type}** `{uuid[:8]}`{marker}: {node.content} "
                f"(metadata={node.metadata})"
            )
            log_nodes: list[Node] = []
            if source_logs and uuid in source_logs:
                log_nodes = source_logs[uuid]
            else:
                for log_id in reversed(node.journal_entry_ids):
                    log_node = self.nodes.get(log_id)
                    if log_node and log_node.node_type == "log":
                        log_nodes.append(log_node)
            for log_node in log_nodes:
                snippet = (log_node.content or "")[:SOURCE_LOG_SNIPPET_LENGTH]
                lines.append(f'  [source] "{snippet}"')
        lines.append("\n## Relationships\n")
        for edge in self.edges:
            subj = self.nodes.get(edge.subject_uuid)
            obj = self.nodes.get(edge.object_uuid)
            subj_label = subj.content[:40] if subj else edge.subject_uuid[:8]
            obj_label = obj.content[:40] if obj else edge.object_uuid[:8]
            lines.append(f"- {subj_label} --[{edge.predicate}]--> {obj_label}")
        return "\n".join(lines)
```

- [ ] **Step 4: Run the full `test_node_models.py` suite**

Run: `./.venv/bin/pytest tests/test_node_models.py -v`
Expected: all tests PASS

- [ ] **Step 5: Lint**

Run: `./.venv/bin/ruff format . && ./.venv/bin/ruff check --fix .`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add lethe/models/node.py tests/test_node_models.py
git commit -m "feat: enrich to_markdown with source log context via source_logs parameter"
```

---

### Task 4: Wire `fetch_source_logs` into the summarize router

**Files:**
- Modify: `lethe/routers/graph.py`

- [ ] **Step 1: Add the import for `fetch_source_logs` at the top of `lethe/routers/graph.py`**

After the existing `from lethe.graph.traverse import graph_expand` line, add:

```python
from lethe.graph.source_fetch import fetch_source_logs
```

- [ ] **Step 2: Fetch source logs from the combined graph and pass to `final_md`**

In the `summarize` handler, find the line:

```python
final_md = combined.to_markdown(req.seed_ids)
```

Replace it with:

```python
source_logs = await fetch_source_logs(
    entity_nodes=combined.nodes,
    db=db,
    config=config,
)
log.info("summarize:source_logs fetched=%d entities_with_logs=%d", len(combined.nodes), len(source_logs))
final_md = combined.to_markdown(req.seed_ids, source_logs=source_logs)
```

- [ ] **Step 3: Run the full test suite to confirm nothing regressed**

Run: `./.venv/bin/pytest tests/ -v --tb=short`
Expected: all tests PASS

- [ ] **Step 4: Lint**

Run: `./.venv/bin/ruff format . && ./.venv/bin/ruff check --fix .`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add lethe/routers/graph.py
git commit -m "feat: wire source log enrichment into summarize pipeline"
```

---

### Task 5: Update wiki

**Files:**
- Modify: `wiki/algorithms.md`
- Modify: `wiki/log.md`

- [ ] **Step 1: Add source enrichment section to `wiki/algorithms.md`**

After Section 6 (Graph Summarization), insert:

```markdown
---

## 6a. Source Log Enrichment (`lethe/graph/source_fetch.py`)

Called in the summarize pipeline after BFS expansion, before the final LLM pass.

```
fetch_source_logs(entity_nodes, db, config, max_per_node=2, max_total=30)
```

1. For each entity node in the expanded graph, take the last `max_per_node` entries from `journal_entry_ids` (most recent first — IDs are appended in insertion order)
2. Deduplicate across all entities; cap at `max_total` total log IDs
3. Batch-fetch from Firestore nodes collection
4. Return `entity_uuid → [log_node, ...]`

Result is passed to `GraphExpandResponse.to_markdown()` as `source_logs`. The markdown then renders each entity's source log content inline:

```
- **person** `abc12345` [SEED]: Jack Strohm (metadata={})
  [source] "Started building Lethe last month, it's a graph-based memory system..."
  [source] "Working on the summarization pipeline today..."
```

This gives the final summarization LLM both the structured fact (compressed, precise) and the original prose (expressive, contextual).

Constants: `SOURCE_LOGS_MAX_PER_NODE = 2`, `SOURCE_LOGS_MAX_TOTAL = 30`, `SOURCE_LOG_SNIPPET_LENGTH = 250`
```

- [ ] **Step 2: Append to `wiki/log.md`**

Add one line at the end:

```
2026-04-24: [algorithms] Added §6a source log enrichment — fetch_source_logs() wired into summarize pipeline
```

- [ ] **Step 3: Commit**

```bash
git add wiki/algorithms.md wiki/log.md
git commit -m "docs: update wiki for source log enrichment feature"
```

---

## Self-Review

**Spec coverage:**
- ✅ Source text retrieved from log nodes via `journal_entry_ids`
- ✅ Enriched context in `to_markdown()` with `[structured fact]` node line + `[source] "..."` lines
- ✅ Token cost controlled by `SOURCE_LOGS_MAX_PER_NODE` and `SOURCE_LOGS_MAX_TOTAL`
- ✅ Fallback: if no source logs fetched, `to_markdown()` falls back to log nodes already in `self.nodes` (maintains backward compat)
- ✅ Draft summarization pass (pass 1) unchanged — source enrichment only on final context
- ✅ Wiki updated

**Placeholder scan:** None found.

**Type consistency:**
- `fetch_source_logs` returns `dict[str, list[Node]]`
- `to_markdown` receives `source_logs: dict[str, list[Node]] | None`
- `combined.nodes` is `dict[str, Node]` — matches `entity_nodes` parameter type
- All consistent.
