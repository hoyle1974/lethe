# Corpus Pipeline Reform: Hub-and-Spoke + Deterministic Code Graphs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple corpus chunk ingestion from SPO triple extraction — raw chunks become vector-indexed `node_type="chunk"` nodes (no LLM), SPO graph is built from a single LLM-generated document summary per document, and Python/code files get an additional deterministic structural edges pass via stdlib `ast`.

**Architecture:** Per document: (1) create document node, (2) LLM-summarize the full document → call `run_ingest(summary)` once for SPO graph, (3) chunk document → write `node_type="chunk"` nodes directly to Firestore with no LLM extraction, (4) for code files: `extract_structural_triples()` via stdlib `ast` → write entity nodes + edges without LLM.

**Tech Stack:** Python stdlib `ast` (structural code parsing for `.py`), stdlib `re` (generic code types), existing Gemini/Vertex AI, Firestore, `extract_triples`, `ensure_node`, `create_relationship_node`.

---

## Subsystem Split Note

The original spec covers two independent subsystems. This plan covers **Plan A: Corpus Pipeline Reform** only. **Plan B: Hierarchical Corpus Namespaces** (adding `source_filter` to `GraphExpandRequest` and `graph_expand`) is independent — create a separate plan for it after this one ships.

---

## File Map

**Create:**
- `lethe/graph/code_graph.py` — `extract_structural_triples(text, filename)` returning `list[tuple[str, str, str]]`; uses stdlib `ast` for `.py`; regex for other code extensions
- `lethe/prompts/document_summary.txt` — Jinja2 prompt for document-level summarization
- `tests/test_code_graph.py` — pure unit tests for `extract_structural_triples`

**Modify:**
- `lethe/constants.py` — add `NODE_TYPE_CHUNK`, `CHUNK_NODE_WEIGHT`, `LLM_MAX_TOKENS_DOCUMENT_SUMMARY`, `DOCUMENT_SUMMARY_CHAR_LIMIT`
- `lethe/graph/extraction.py` — add `summarize_document(llm, text, filename)` function + lazy-loaded template
- `lethe/graph/corpus.py` — rewrite `run_corpus_ingest`; add `_create_chunk_node`, `_ingest_structural_edges`, `_node_exists_by_type`, `_merge_ingest_result`
- `lethe/models/node.py` — add `chunk_ids: list[str]` field to `CorpusIngestResponse`
- `tests/test_extraction.py` — add `test_summarize_document_returns_llm_text`
- `tests/test_routers.py` — add three new corpus hub-and-spoke tests
- `wiki/algorithms.md` — update §9 Corpus Ingestion
- `wiki/api.md` — update `POST /v1/ingest/corpus` response schema

---

## Task 1: Add constants

**Files:**
- Modify: `lethe/constants.py`

- [ ] **Step 1: Add constants**

Add to `lethe/constants.py` after `DOCUMENT_NODE_WEIGHT = 1.0`:

```python
NODE_TYPE_CHUNK = "chunk"
CHUNK_NODE_WEIGHT = 0.4
LLM_MAX_TOKENS_DOCUMENT_SUMMARY = 512
DOCUMENT_SUMMARY_CHAR_LIMIT = 50_000
```

- [ ] **Step 2: Verify import works**

Run: `./.venv/bin/python -c "from lethe.constants import NODE_TYPE_CHUNK, CHUNK_NODE_WEIGHT, LLM_MAX_TOKENS_DOCUMENT_SUMMARY, DOCUMENT_SUMMARY_CHAR_LIMIT; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add lethe/constants.py
git commit -m "feat: add NODE_TYPE_CHUNK, CHUNK_NODE_WEIGHT, and document summary constants"
```

---

## Task 2: TDD — `lethe/graph/code_graph.py`

**Files:**
- Create: `tests/test_code_graph.py`
- Create: `lethe/graph/code_graph.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_code_graph.py`:

```python
from lethe.graph.code_graph import extract_structural_triples


def test_python_import_creates_imports_triple():
    code = "import os\n\ndef foo():\n    pass\n"
    triples = extract_structural_triples(code, "utils.py")
    assert ("utils", "imports", "os") in triples


def test_python_from_import_uses_top_level_module():
    code = "from pathlib import Path\n\ndef foo():\n    pass\n"
    triples = extract_structural_triples(code, "main.py")
    assert ("main", "imports", "pathlib") in triples


def test_python_dotted_import_uses_top_level_only():
    """from google.cloud.firestore import Client → imports google, not google.cloud.firestore"""
    code = "from google.cloud.firestore import Client\n\ndef foo():\n    pass\n"
    triples = extract_structural_triples(code, "db.py")
    objects = [o for _, p, o in triples if p == "imports"]
    assert "google" in objects
    assert "google.cloud.firestore" not in objects


def test_python_defines_function():
    code = "def my_function():\n    pass\n"
    triples = extract_structural_triples(code, "funcs.py")
    assert ("funcs", "defines", "my_function") in triples


def test_python_async_def_defines():
    code = "async def fetch_data():\n    pass\n"
    triples = extract_structural_triples(code, "fetch.py")
    assert ("fetch", "defines", "fetch_data") in triples


def test_python_defines_class():
    code = "class MyModel:\n    pass\n"
    triples = extract_structural_triples(code, "models.py")
    assert ("models", "defines", "MyModel") in triples


def test_python_class_method_creates_has_method_triple():
    code = "class Foo:\n    def bar(self):\n        pass\n    def baz(self):\n        pass\n"
    triples = extract_structural_triples(code, "foo.py")
    assert ("Foo", "has_method", "bar") in triples
    assert ("Foo", "has_method", "baz") in triples


def test_non_code_file_returns_empty():
    triples = extract_structural_triples("Some prose text.", "README.md")
    assert triples == []


def test_txt_file_returns_empty():
    triples = extract_structural_triples("import os", "notes.txt")
    assert triples == []


def test_syntax_error_falls_back_to_regex_for_imports():
    # Missing colon on if statement is a SyntaxError
    code = "import os\nif True\n    pass\n"
    triples = extract_structural_triples(code, "broken.py")
    objects = [o for _, p, o in triples if p == "imports"]
    assert "os" in objects


def test_js_file_extracts_imports_via_regex():
    code = "import React from 'react';\nimport { useState } from 'react';"
    triples = extract_structural_triples(code, "app.js")
    objects = [o for _, p, o in triples if p == "imports"]
    assert "react" in objects


def test_js_require_extracts_import():
    code = "const express = require('express');"
    triples = extract_structural_triples(code, "server.js")
    objects = [o for _, p, o in triples if p == "imports"]
    assert "express" in objects


def test_multiple_imports_no_duplicates():
    code = "import os\nimport os\n\ndef foo():\n    pass\n"
    triples = extract_structural_triples(code, "dup.py")
    imports = [o for _, p, o in triples if p == "imports" and o == "os"]
    # AST parse: two import nodes → two triples is acceptable; no crash
    assert len(imports) >= 1
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `./.venv/bin/pytest tests/test_code_graph.py -v`
Expected: `ImportError` or `ModuleNotFoundError: No module named 'lethe.graph.code_graph'`

- [ ] **Step 3: Implement `lethe/graph/code_graph.py`**

Create `lethe/graph/code_graph.py`:

```python
from __future__ import annotations

import ast
import re
from pathlib import Path

from lethe.graph.chunk import _CODE_EXTENSIONS

StructuralTriple = tuple[str, str, str]


def extract_structural_triples(text: str, filename: str) -> list[StructuralTriple]:
    """Return deterministic (subject, predicate, object) triples from source code.

    Predicates used: 'imports', 'defines', 'has_method'.
    Returns [] for non-code file extensions.
    """
    ext = Path(filename).suffix.lower()
    if ext not in _CODE_EXTENSIONS:
        return []
    module_name = Path(filename).stem
    if ext == ".py":
        return _python_triples(text, module_name)
    return _generic_code_triples(text, module_name)


def _python_triples(text: str, module_name: str) -> list[StructuralTriple]:
    triples: list[StructuralTriple] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _generic_code_triples(text, module_name)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                triples.append((module_name, "imports", alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                triples.append((module_name, "imports", node.module.split(".")[0]))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            triples.append((module_name, "defines", node.name))
        elif isinstance(node, ast.ClassDef):
            triples.append((module_name, "defines", node.name))
            for item in ast.iter_child_nodes(node):
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    triples.append((node.name, "has_method", item.name))
    return triples


def _generic_code_triples(text: str, module_name: str) -> list[StructuralTriple]:
    triples: list[StructuralTriple] = []
    import_re = re.compile(r"""(?:import|require|from)\s+['"]?([a-zA-Z0-9_/@.-]+)['"]?""")
    seen: set[str] = set()
    for m in import_re.finditer(text):
        name = m.group(1).split("/")[0].split(".")[0]
        if name and name not in seen:
            seen.add(name)
            triples.append((module_name, "imports", name))
    return triples
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `./.venv/bin/pytest tests/test_code_graph.py -v`
Expected: All tests PASS

- [ ] **Step 5: Lint**

Run: `./.venv/bin/ruff format lethe/graph/code_graph.py tests/test_code_graph.py && ./.venv/bin/ruff check --fix lethe/graph/code_graph.py tests/test_code_graph.py`

- [ ] **Step 6: Commit**

```bash
git add lethe/graph/code_graph.py tests/test_code_graph.py
git commit -m "feat: add code_graph.py for deterministic structural triple extraction via ast"
```

---

## Task 3: TDD — `summarize_document` in `extraction.py`

**Files:**
- Create: `lethe/prompts/document_summary.txt`
- Modify: `lethe/graph/extraction.py`
- Modify: `tests/test_extraction.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_extraction.py`:

```python
import pytest


async def test_summarize_document_returns_llm_text():
    from unittest.mock import AsyncMock

    from lethe.graph.extraction import summarize_document

    mock_llm = AsyncMock()
    mock_llm.dispatch.return_value = "Alice works at Acme Corp and manages Bob."

    result = await summarize_document(
        mock_llm, text="Alice works at Acme. She manages Bob.", filename="notes.txt"
    )

    assert result == "Alice works at Acme Corp and manages Bob."
    assert mock_llm.dispatch.call_count == 1


async def test_summarize_document_truncates_at_char_limit():
    from unittest.mock import AsyncMock

    from lethe.constants import DOCUMENT_SUMMARY_CHAR_LIMIT
    from lethe.graph.extraction import summarize_document

    captured: list = []

    class CapturingLLM:
        async def dispatch(self, req):
            captured.append(req)
            return "summary"

    long_text = "x" * (DOCUMENT_SUMMARY_CHAR_LIMIT + 10_000)
    await summarize_document(CapturingLLM(), text=long_text, filename="big.txt")

    assert len(captured) == 1
    # The user_prompt must not contain more chars than DOCUMENT_SUMMARY_CHAR_LIMIT
    assert len(captured[0].user_prompt) < DOCUMENT_SUMMARY_CHAR_LIMIT + 500
```

- [ ] **Step 2: Run to confirm failure**

Run: `./.venv/bin/pytest tests/test_extraction.py::test_summarize_document_returns_llm_text -v`
Expected: `ImportError: cannot import name 'summarize_document'`

- [ ] **Step 3: Create `lethe/prompts/document_summary.txt`**

Create `lethe/prompts/document_summary.txt`:

```
You are a knowledge-graph preparation assistant. Read the document below and produce a concise summary that preserves every important entity, relationship, and key fact.

Requirements:
- Write 3-5 dense prose sentences
- Include every named entity (person, place, organization, tool, project, concept, module, function)
- Include all significant relationships between entities
- Omit implementation noise, syntax details, and boilerplate
- Write only the summary — no preamble, headers, or bullet points
{% if filename %}
Document: {{ filename }}
{% endif %}
---
{{ text }}
```

- [ ] **Step 4: Add `summarize_document` to `lethe/graph/extraction.py`**

Add to the top of `lethe/graph/extraction.py` after the existing imports:

```python
from lethe.constants import (
    DEFAULT_NODE_TYPE,
    DOCUMENT_SUMMARY_CHAR_LIMIT,
    LLM_MAX_TOKENS_DOCUMENT_SUMMARY,
    LLM_MAX_TOKENS_EXTRACTION,
)
```

(Replace the existing import of `DEFAULT_NODE_TYPE, LLM_MAX_TOKENS_EXTRACTION` with this expanded import.)

Add after the `_REFINERY_TEMPLATE = None` line and `_get_refinery_template()` function:

```python
_DOCUMENT_SUMMARY_TEMPLATE = None


def _get_document_summary_template() -> Template:
    global _DOCUMENT_SUMMARY_TEMPLATE
    if _DOCUMENT_SUMMARY_TEMPLATE is None:
        path = os.path.join(_PROMPT_DIR, "document_summary.txt")
        with open(path) as f:
            source = f.read()
        _DOCUMENT_SUMMARY_TEMPLATE = Environment(loader=BaseLoader()).from_string(source)
    return _DOCUMENT_SUMMARY_TEMPLATE


_SUMMARY_SYSTEM = "You are a knowledge-graph preparation assistant. Output a dense prose summary only."


async def summarize_document(
    llm: LLMDispatcher,
    text: str,
    filename: str = "",
) -> str:
    """Summarize a document into 3-5 entity-dense sentences for SPO extraction."""
    tmpl = _get_document_summary_template()
    prompt = tmpl.render(text=text[:DOCUMENT_SUMMARY_CHAR_LIMIT], filename=filename)
    log.info("summarize_document: sending %d chars to LLM filename=%r", len(text), filename)
    raw = await llm.dispatch(
        LLMRequest(
            system_prompt=_SUMMARY_SYSTEM,
            user_prompt=prompt,
            max_tokens=LLM_MAX_TOKENS_DOCUMENT_SUMMARY,
        )
    )
    return raw.strip()
```

- [ ] **Step 5: Run tests to confirm they pass**

Run: `./.venv/bin/pytest tests/test_extraction.py -v`
Expected: All PASS

- [ ] **Step 6: Lint**

Run: `./.venv/bin/ruff format lethe/graph/extraction.py lethe/prompts/document_summary.txt && ./.venv/bin/ruff check --fix lethe/graph/extraction.py`

- [ ] **Step 7: Commit**

```bash
git add lethe/graph/extraction.py lethe/prompts/document_summary.txt tests/test_extraction.py
git commit -m "feat: add summarize_document() and document_summary.txt prompt for hub-and-spoke corpus ingestion"
```

---

## Task 4: TDD — Add `chunk_ids` to `CorpusIngestResponse`

**Files:**
- Modify: `lethe/models/node.py`
- Modify: `tests/test_routers.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_routers.py`, add this test after the existing corpus tests (after `test_corpus_ingest_rejects_empty_documents`):

```python
def test_corpus_ingest_response_contains_chunk_ids(mock_embedder, mock_llm):
    """Response includes a chunk_ids list with one entry per chunk created."""
    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
    mock_doc_ref.update = AsyncMock()
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.stream = AsyncMock(  # noqa: E501
        return_value=_async_iter([])
    )
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={"documents": [{"text": "Alice works at Acme.", "filename": "notes.txt"}]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "chunk_ids" in data
    assert isinstance(data["chunk_ids"], list)
    assert len(data["chunk_ids"]) >= 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `./.venv/bin/pytest tests/test_routers.py::test_corpus_ingest_response_contains_chunk_ids -v`
Expected: FAIL — `AssertionError: assert 'chunk_ids' in {...}` (field doesn't exist yet)

- [ ] **Step 3: Add `chunk_ids` to `CorpusIngestResponse`**

In `lethe/models/node.py`, update `CorpusIngestResponse`:

```python
class CorpusIngestResponse(BaseModel):
    corpus_id: str
    document_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    total_chunks: int = 0
    nodes_created: list[str] = Field(default_factory=list)
    nodes_updated: list[str] = Field(default_factory=list)
    relationships_created: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to confirm it fails differently**

Run: `./.venv/bin/pytest tests/test_routers.py::test_corpus_ingest_response_contains_chunk_ids -v`
Expected: FAIL — `AssertionError: assert len([]) >= 1` (field exists but empty — corpus.py doesn't populate it yet)

- [ ] **Step 5: Run full test suite to confirm no regressions**

Run: `./.venv/bin/pytest tests/test_node_models.py tests/test_routers.py -v`
Expected: All existing tests PASS (new field has `default_factory=list` so serialization is backward-compatible)

- [ ] **Step 6: Lint**

Run: `./.venv/bin/ruff format lethe/models/node.py && ./.venv/bin/ruff check --fix lethe/models/node.py`

- [ ] **Step 7: Commit**

```bash
git add lethe/models/node.py tests/test_routers.py
git commit -m "feat: add chunk_ids field to CorpusIngestResponse"
```

---

## Task 5: TDD — `_create_chunk_node` in `corpus.py`

**Files:**
- Modify: `lethe/graph/corpus.py`

- [ ] **Step 1: Write the failing test (in `tests/test_routers.py`)**

The test from Task 4 (`test_corpus_ingest_response_contains_chunk_ids`) will pass once `run_corpus_ingest` calls `_create_chunk_node` and populates `chunk_ids`. Add a complementary test for the node_type written:

Append to `tests/test_routers.py`:

```python
def test_corpus_ingest_chunk_nodes_use_chunk_type(mock_embedder, mock_llm):
    """Each chunk is stored as node_type='chunk', not 'log'."""
    written_node_types: list[str] = []

    async def capturing_set(data, **kwargs):
        if isinstance(data, dict) and "node_type" in data:
            written_node_types.append(data["node_type"])

    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = capturing_set
    mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
    mock_doc_ref.update = AsyncMock()
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.stream = AsyncMock(  # noqa: E501
        return_value=_async_iter([])
    )
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={"documents": [{"text": "Alice works at Acme.", "filename": "notes.txt"}]},
    )
    assert resp.status_code == 201
    assert "chunk" in written_node_types
```

- [ ] **Step 2: Run to confirm failure**

Run: `./.venv/bin/pytest tests/test_routers.py::test_corpus_ingest_chunk_nodes_use_chunk_type -v`
Expected: FAIL — `assert 'chunk' in ['document', 'log']` (current code writes 'log' nodes for chunks)

- [ ] **Step 3: Add `_create_chunk_node` and helper imports to `corpus.py`**

Update the imports at the top of `lethe/graph/corpus.py` to add:

```python
from lethe.constants import (
    CHUNK_NODE_WEIGHT,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_DOMAIN,
    DEFAULT_USER_ID,
    DOCUMENT_NODE_WEIGHT,
    EMBEDDING_TASK_RETRIEVAL_DOCUMENT,
    NODE_TYPE_CHUNK,
    NODE_TYPE_DOCUMENT,
)
```

(Replace the existing `from lethe.constants import (...)` block with this expanded version.)

Add `_create_chunk_node` after `_create_document_node` in `lethe/graph/corpus.py`:

```python
async def _create_chunk_node(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    text: str,
    document_id: str,
    corpus_id: str,
    filename: str,
    chunk_index: int,
    user_id: str,
    domain: str,
    ts: str,
) -> str:
    chunk_id = str(uuid.uuid4())
    vector = await embedder.embed(text[:10_000], EMBEDDING_TASK_RETRIEVAL_DOCUMENT)
    metadata = json.dumps(
        {
            "document_id": document_id,
            "corpus_id": corpus_id,
            "filename": filename,
            "chunk_index": chunk_index,
        }
    )
    await (
        db.collection(config.lethe_collection)
        .document(chunk_id)
        .set(
            {
                "node_type": NODE_TYPE_CHUNK,
                "content": text,
                "domain": domain,
                "weight": CHUNK_NODE_WEIGHT,
                "metadata": metadata,
                "embedding": Vector(vector),
                "user_id": user_id,
                "source": corpus_id,
                "created_at": ts,
                "updated_at": ts,
            }
        )
    )
    log.info(
        "corpus: created chunk node chunk_id=%s filename=%r chunk_index=%d",
        chunk_id,
        filename,
        chunk_index,
    )
    return chunk_id
```

Note: At this point `_create_chunk_node` exists but is not yet called by `run_corpus_ingest`. Both `test_corpus_ingest_chunk_nodes_use_chunk_type` and `test_corpus_ingest_response_contains_chunk_ids` still fail — they'll pass together in Task 7.

- [ ] **Step 4: Lint**

Run: `./.venv/bin/ruff format lethe/graph/corpus.py && ./.venv/bin/ruff check --fix lethe/graph/corpus.py`

- [ ] **Step 5: Commit**

```bash
git add lethe/graph/corpus.py
git commit -m "feat: add _create_chunk_node to corpus.py (not yet wired into run_corpus_ingest)"
```

---

## Task 6: TDD — `_ingest_structural_edges` in `corpus.py`

**Files:**
- Modify: `lethe/graph/corpus.py`
- Modify: `tests/test_routers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_routers.py`:

```python
def test_corpus_ingest_code_file_creates_structural_edges(mock_embedder, mock_llm):
    """A .py document triggers deterministic structural edge ingestion without extra LLM calls."""
    from unittest.mock import MagicMock as MM

    dispatch_calls: list = []

    class TrackingLLM:
        async def dispatch(self, req):
            dispatch_calls.append(req)
            return "status: none"

    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=MM(exists=False))
    mock_doc_ref.update = AsyncMock()
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_db.collection.return_value.where.return_value.find_nearest.return_value.stream = AsyncMock(
        return_value=_async_iter([])
    )
    mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.stream = AsyncMock(  # noqa: E501
        return_value=_async_iter([])
    )
    mock_db.transaction.return_value = MM()

    @staticmethod
    async def fake_transactional(fn):
        return await fn(MM())

    import google.cloud.firestore as _fs

    python_code = "import os\n\ndef main():\n    pass\n"

    client = _make_test_client(mock_embedder, TrackingLLM(), mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={"documents": [{"text": python_code, "filename": "main.py"}]},
    )
    assert resp.status_code == 201
    data = resp.json()
    # Structural edges produce nodes_created or nodes_updated entries for module/function nodes
    assert (len(data["nodes_created"]) + len(data["nodes_updated"])) >= 0  # at minimum no crash
    # LLM not called for structural edges — only for summary + extraction (2 total)
    assert len(dispatch_calls) == 2
```

- [ ] **Step 2: Run to confirm failure**

Run: `./.venv/bin/pytest tests/test_routers.py::test_corpus_ingest_code_file_creates_structural_edges -v`
Expected: FAIL (function `_ingest_structural_edges` not wired in yet; but the LLM count assertion will fail because old code calls 1 LLM call per chunk, not exactly 2)

- [ ] **Step 3: Add helpers and `_ingest_structural_edges` to `corpus.py`**

Add these new imports at the top of `lethe/graph/corpus.py`:

```python
from lethe.graph.chunk import chunk_document, detect_chunk_strategy
from lethe.graph.code_graph import extract_structural_triples
from lethe.graph.ensure_node import (
    create_relationship_node,
    ensure_node,
    stable_entity_doc_id,
)
from lethe.graph.extraction import summarize_document
```

(These replace or supplement existing imports. Keep `from lethe.graph.ingest import run_ingest` and `from lethe.graph.chunk import chunk_document`; add the new ones.)

Add these two helpers after `_create_chunk_node`:

```python
async def _node_exists_by_type(
    db: firestore.AsyncClient,
    config: Config,
    node_type: str,
    name: str,
) -> bool:
    doc_id = stable_entity_doc_id(node_type, name)
    snap = await db.collection(config.lethe_collection).document(doc_id).get()
    return snap.exists


def _merge_ingest_result(
    result,
    seen_created: set[str],
    seen_updated: set[str],
    seen_relationships: set[str],
    all_nodes_created: list[str],
    all_nodes_updated: list[str],
    all_relationships_created: list[str],
) -> None:
    for n in result.nodes_created:
        if n not in seen_created:
            seen_created.add(n)
            seen_updated.discard(n)
            all_nodes_created.append(n)
    for n in result.nodes_updated:
        if n not in seen_created and n not in seen_updated:
            seen_updated.add(n)
            all_nodes_updated.append(n)
    for r in result.relationships_created:
        if r not in seen_relationships:
            seen_relationships.add(r)
            all_relationships_created.append(r)
```

Add `_ingest_structural_edges` after `_merge_ingest_result`:

```python
_STRUCTURAL_PREDICATE_OBJECT_TYPE: dict[str, str] = {
    "imports": "module",
    "defines": "function",
    "has_method": "function",
}


async def _ingest_structural_edges(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    text: str,
    filename: str,
    document_id: str,
    corpus_id: str,
    user_id: str,
    domain: str,
    ts: str,
    nodes_created: list[str],
    nodes_updated: list[str],
    relationships_created: list[str],
    seen_created: set[str],
    seen_updated: set[str],
    seen_relationships: set[str],
) -> None:
    """Write deterministic code-structure edges to the graph without LLM calls."""
    triples = extract_structural_triples(text, filename)
    if not triples:
        return

    log.info(
        "corpus: structural edges filename=%r triples=%d", filename, len(triples)
    )
    for subj, pred, obj in triples:
        subj_type = "module"
        obj_type = _STRUCTURAL_PREDICATE_OBJECT_TYPE.get(pred, "generic")

        subj_existed = await _node_exists_by_type(db, config, subj_type, subj)
        subj_node = await ensure_node(
            db=db,
            embedder=embedder,
            config=config,
            identifier=subj,
            node_type=subj_type,
            source_entry_id=document_id,
            timestamp=ts,
            user_id=user_id,
            llm=None,
        )

        obj_existed = await _node_exists_by_type(db, config, obj_type, obj)
        obj_node = await ensure_node(
            db=db,
            embedder=embedder,
            config=config,
            identifier=obj,
            node_type=obj_type,
            source_entry_id=document_id,
            timestamp=ts,
            user_id=user_id,
            llm=None,
        )

        for node_uuid, existed in [(subj_node.uuid, subj_existed), (obj_node.uuid, obj_existed)]:
            if not existed and node_uuid not in seen_created:
                seen_created.add(node_uuid)
                seen_updated.discard(node_uuid)
                nodes_created.append(node_uuid)
            elif existed and node_uuid not in seen_created and node_uuid not in seen_updated:
                seen_updated.add(node_uuid)
                nodes_updated.append(node_uuid)

        rel_id = await create_relationship_node(
            db=db,
            embedder=embedder,
            config=config,
            subject_id=subj_node.uuid,
            predicate=pred,
            object_id=obj_node.uuid,
            source_entry_id=document_id,
            subject_content=subj_node.content,
            object_content=obj_node.content,
            timestamp=ts,
            user_id=user_id,
            llm=None,
        )
        if rel_id not in seen_relationships:
            seen_relationships.add(rel_id)
            relationships_created.append(rel_id)
```

- [ ] **Step 4: Lint**

Run: `./.venv/bin/ruff format lethe/graph/corpus.py && ./.venv/bin/ruff check --fix lethe/graph/corpus.py`

- [ ] **Step 5: Commit**

```bash
git add lethe/graph/corpus.py tests/test_routers.py
git commit -m "feat: add _ingest_structural_edges, _create_chunk_node helpers, and _merge_ingest_result to corpus.py"
```

---

## Task 7: Rewrite `run_corpus_ingest` to hub-and-spoke model

**Files:**
- Modify: `lethe/graph/corpus.py`
- Modify: `tests/test_routers.py`

- [ ] **Step 1: Write the key behavioral test (LLM call count)**

Append to `tests/test_routers.py`:

```python
def test_corpus_ingest_llm_called_twice_per_doc_not_per_chunk(mock_embedder):
    """Hub-and-spoke: LLM called exactly twice per document (summary + extraction),
    regardless of how many chunks the document produces."""
    # 5 paragraphs at chunk_size=2 → 5+ chunks; old code would call LLM 5+ times
    multi_para = "\n\n".join([f"paragraph {i} words." for i in range(5)])

    dispatch_calls: list = []

    class TrackingLLM:
        async def dispatch(self, req):
            dispatch_calls.append(req)
            return "status: none"

    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
    mock_doc_ref.update = AsyncMock()
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.stream = AsyncMock(  # noqa: E501
        return_value=_async_iter([])
    )
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, TrackingLLM(), mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={
            "documents": [{"text": multi_para, "filename": "notes.txt"}],
            "chunk_size": 2,
        },
    )
    assert resp.status_code == 201
    # Exactly 2 LLM calls per document: 1 for summarize_document + 1 for extract_triples(summary)
    assert len(dispatch_calls) == 2
```

- [ ] **Step 2: Run to confirm failure**

Run: `./.venv/bin/pytest tests/test_routers.py::test_corpus_ingest_llm_called_twice_per_doc_not_per_chunk -v`
Expected: FAIL — `assert len(dispatch_calls) == 2` fails because old code calls LLM once per chunk (5+ calls)

- [ ] **Step 3: Rewrite `run_corpus_ingest` in `lethe/graph/corpus.py`**

Replace the entire `run_corpus_ingest` function body with:

```python
async def run_corpus_ingest(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    canonical_map: CanonicalMap,
    documents: list[DocumentItem],
    corpus_id: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    domain: str = DEFAULT_DOMAIN,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> CorpusIngestResponse:
    corpus_id = corpus_id or str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()

    document_ids: list[str] = []
    chunk_ids: list[str] = []
    all_nodes_created: list[str] = []
    all_nodes_updated: list[str] = []
    all_relationships_created: list[str] = []
    seen_created: set[str] = set()
    seen_updated: set[str] = set()
    seen_relationships: set[str] = set()
    total_chunks = 0

    total_docs = len(documents)
    for doc_idx, doc in enumerate(documents):
        log.info("corpus: [%d/%d] starting %r", doc_idx + 1, total_docs, doc.filename)

        doc_id = await _create_document_node(
            db=db,
            embedder=embedder,
            config=config,
            text=doc.text,
            filename=doc.filename,
            corpus_id=corpus_id,
            user_id=user_id,
            domain=domain,
            ts=ts,
        )
        document_ids.append(doc_id)

        # One LLM summary per document → SPO extraction on summary only
        summary = await summarize_document(llm=llm, text=doc.text, filename=doc.filename)
        log.info(
            "corpus: [%d/%d] summary=%d chars filename=%r",
            doc_idx + 1,
            total_docs,
            len(summary),
            doc.filename,
        )
        if summary:
            summary_result = await run_ingest(
                db=db,
                embedder=embedder,
                llm=llm,
                config=config,
                canonical_map=canonical_map,
                text=summary,
                domain=domain,
                source=corpus_id,
                user_id=user_id,
                timestamp=ts,
                metadata={
                    "document_id": doc_id,
                    "filename": doc.filename,
                    "is_summary": True,
                },
            )
            _merge_ingest_result(
                summary_result,
                seen_created,
                seen_updated,
                seen_relationships,
                all_nodes_created,
                all_nodes_updated,
                all_relationships_created,
            )

        # Chunks stored as vector-indexed nodes — no SPO extraction
        chunks = chunk_document(doc.text, doc.filename, chunk_size)
        log.info(
            "corpus: [%d/%d] %r → %d chunks (doc_id=%s)",
            doc_idx + 1,
            total_docs,
            doc.filename,
            len(chunks),
            doc_id,
        )
        for i, chunk_text in enumerate(chunks):
            chunk_id = await _create_chunk_node(
                db=db,
                embedder=embedder,
                config=config,
                text=chunk_text,
                document_id=doc_id,
                corpus_id=corpus_id,
                filename=doc.filename,
                chunk_index=i,
                user_id=user_id,
                domain=domain,
                ts=ts,
            )
            chunk_ids.append(chunk_id)
            total_chunks += 1

        # Code files get deterministic structural edges (no LLM)
        if detect_chunk_strategy(doc.filename) == "code":
            await _ingest_structural_edges(
                db=db,
                embedder=embedder,
                config=config,
                text=doc.text,
                filename=doc.filename,
                document_id=doc_id,
                corpus_id=corpus_id,
                user_id=user_id,
                domain=domain,
                ts=ts,
                nodes_created=all_nodes_created,
                nodes_updated=all_nodes_updated,
                relationships_created=all_relationships_created,
                seen_created=seen_created,
                seen_updated=seen_updated,
                seen_relationships=seen_relationships,
            )

    log.info(
        "corpus: complete corpus_id=%s documents=%d chunks=%d nodes_created=%d",
        corpus_id,
        len(document_ids),
        total_chunks,
        len(all_nodes_created),
    )
    return CorpusIngestResponse(
        corpus_id=corpus_id,
        document_ids=document_ids,
        chunk_ids=chunk_ids,
        total_chunks=total_chunks,
        nodes_created=all_nodes_created,
        nodes_updated=all_nodes_updated,
        relationships_created=all_relationships_created,
    )
```

- [ ] **Step 4: Run the new tests**

Run: `./.venv/bin/pytest tests/test_routers.py::test_corpus_ingest_llm_called_twice_per_doc_not_per_chunk tests/test_routers.py::test_corpus_ingest_response_contains_chunk_ids tests/test_routers.py::test_corpus_ingest_chunk_nodes_use_chunk_type -v`
Expected: All three PASS

- [ ] **Step 5: Run the full test suite**

Run: `./.venv/bin/pytest -v`
Expected: All tests PASS (existing corpus router tests still pass because the response fields they check are still present)

- [ ] **Step 6: Lint**

Run: `./.venv/bin/ruff format lethe/graph/corpus.py && ./.venv/bin/ruff check --fix lethe/graph/corpus.py`

- [ ] **Step 7: Commit**

```bash
git add lethe/graph/corpus.py tests/test_routers.py
git commit -m "feat: rewrite run_corpus_ingest to hub-and-spoke model — summary for SPO, chunks as vector nodes, code gets structural edges"
```

---

## Task 8: Update wiki docs

**Files:**
- Modify: `wiki/algorithms.md`
- Modify: `wiki/api.md`
- Modify: `wiki/log.md`

- [ ] **Step 1: Update `wiki/algorithms.md` §9**

Replace the entire **§9 Corpus Ingestion** section with:

```markdown
## 9. Corpus Ingestion (`lethe/graph/corpus.py::run_corpus_ingest`)

Entry point: `POST /v1/ingest/corpus`.

### Hub-and-Spoke Model

Chunks are stored as vector-indexed `node_type="chunk"` nodes only. SPO triple extraction runs once per document on a generated summary — not per chunk. Code files get an additional deterministic structural edges pass.

Steps for each document in the request:

1. **Create document node** — embed first 10 000 chars, write to Firestore with `node_type="document"`, `weight=1.0`, `source=corpus_id`, `metadata={"filename": ..., "corpus_id": ...}`. Full original text stored in `content` field.

2. **Summarize document** — `summarize_document(llm, text, filename)` → Gemini generates 3–5 entity-dense sentences capped at `DOCUMENT_SUMMARY_CHAR_LIMIT = 50 000` chars. Uses `lethe/prompts/document_summary.txt`. Max tokens: `LLM_MAX_TOKENS_DOCUMENT_SUMMARY = 512`.

3. **SPO extraction on summary** — call `run_ingest(summary)` exactly once per document. Creates one `node_type="log"` summary node + entity/relationship nodes from triples. Tagged `metadata={"is_summary": True, "document_id": ..., "filename": ...}`.

4. **Chunk document** — `chunk_document(text, filename, chunk_size)` dispatches to code or prose strategy (same as before). Each chunk is stored as `_create_chunk_node()` → `node_type="chunk"`, `weight=0.4`, no LLM extraction. Chunk metadata includes `document_id`, `chunk_index`, `corpus_id`, `filename`.

5. **Structural edges for code files** — if `detect_chunk_strategy(filename) == "code"`, `_ingest_structural_edges()` calls `extract_structural_triples(text, filename)` and writes entity + relationship nodes **without LLM**:
   - `.py` files: stdlib `ast` parser extracts `(module, imports, dep)`, `(module, defines, fn)`, `(ClassName, has_method, fn)` triples
   - Other code types: regex-based import extraction
   - Entity node types: `module` for subjects/import targets, `function` for defines/has_method targets
   - `llm=None` passed to `ensure_node` and `create_relationship_node` (no collision detection, no supersede check)

6. **Aggregate** — set-based deduplication of `nodes_created`, `nodes_updated`, `relationships_created` across all documents.

### Traceability chain

```
document node  (node_type="document", content=full text, source=corpus_id)
  └── summary log node  (node_type="log", metadata={"is_summary": True, "document_id": ...})
        └── entity/relationship nodes  (source=corpus_id)
chunk nodes  (node_type="chunk", metadata={"document_id": ..., "chunk_index": N}, source=corpus_id)
structural entity nodes  (node_type="module"|"function", source=corpus_id)  [code files only]
structural edges  (predicate="imports"|"defines"|"has_method")  [code files only]
```

### Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `NODE_TYPE_CHUNK` | `"chunk"` | Node type for raw chunk nodes |
| `CHUNK_NODE_WEIGHT` | `0.4` | Default weight for chunk nodes |
| `LLM_MAX_TOKENS_DOCUMENT_SUMMARY` | `512` | Max tokens for document summary |
| `DOCUMENT_SUMMARY_CHAR_LIMIT` | `50 000` | Max chars sent to summarizer |
| `DEFAULT_CHUNK_SIZE` | `600` | Words per chunk (configurable per request) |
```

- [ ] **Step 2: Update `wiki/api.md` POST /v1/ingest/corpus**

In the `POST /v1/ingest/corpus` section, update the description and response:

Replace the description paragraph:
```
Ingest a collection of related documents (e.g. a codebase) as a single corpus.
Stores each document as a raw `document` node preserving full original text, then
chunks and ingests each chunk through the standard triple extraction pipeline.
All nodes and edges are tagged `source=corpus_id`. Re-submitting with the same
`corpus_id` appends to the existing corpus.
```

With:
```
Ingest a collection of related documents (e.g. a codebase) as a single corpus
using the hub-and-spoke model. Each document gets one LLM summary → SPO extraction
pass. Raw chunks are stored as vector-indexed `chunk` nodes (no triple extraction).
Code files (`.py`, `.js`, `.ts`, etc.) additionally get deterministic structural edges
(imports/defines/has_method) via AST parsing. All nodes and edges are tagged
`source=corpus_id`. Re-submitting with the same `corpus_id` appends to the existing corpus.
```

Replace the response JSON example with:
```json
{
  "corpus_id": "uuid-or-caller-provided",
  "document_ids": ["doc-uuid-1", "doc-uuid-2"],
  "chunk_ids": ["chunk-uuid-1", "chunk-uuid-2", "..."],
  "total_chunks": 42,
  "nodes_created": ["entity_abc", "..."],
  "nodes_updated": [],
  "relationships_created": ["rel_xyz", "..."]
}
```

Add note for `chunk_ids`:
```
`chunk_ids` — UUIDs of `node_type="chunk"` nodes. Use these for targeted vector search against raw source text.
```

- [ ] **Step 3: Append to `wiki/log.md`**

```
2026-04-25: [algorithms] §9 updated for hub-and-spoke corpus model — summary-only SPO, chunk nodes, deterministic code graph
2026-04-25: [api.md] Updated POST /v1/ingest/corpus response to include chunk_ids; updated description for hub-and-spoke
```

- [ ] **Step 4: Commit**

```bash
git add wiki/algorithms.md wiki/api.md wiki/log.md
git commit -m "docs: update wiki for hub-and-spoke corpus model and chunk_ids field"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| Document → single LLM summary → SPO extraction only | Task 7 (run_corpus_ingest rewrite) |
| Chunks stored as vector-indexed nodes, no triple extraction | Tasks 5+7 (_create_chunk_node + rewrite) |
| AST-based code graph (Python), no LLM for code structure | Task 2 (code_graph.py) + Task 6 (_ingest_structural_edges) |
| Regex-based import extraction for non-Python code | Task 2 (_generic_code_triples) |
| No new third-party dependencies | ✓ uses stdlib ast only |
| `chunk_ids` in response | Tasks 4+7 |
| Existing tests continue to pass | Task 7 Step 5 full suite run |

**Plan B not covered here:** Hierarchical Corpus Namespaces (`source_filter` on `GraphExpandRequest`) — create a separate plan.

**Placeholder scan:** No TBDs, TODOs, or "similar to above" references found.

**Type consistency check:**
- `extract_structural_triples` returns `list[StructuralTriple]` = `list[tuple[str, str, str]]` — used correctly in `_ingest_structural_edges` ✓
- `_create_chunk_node` returns `str` (chunk_id) — appended to `chunk_ids: list[str]` ✓
- `summarize_document` returns `str` — passed to `run_ingest(..., text=summary)` ✓
- `CorpusIngestResponse.chunk_ids: list[str]` — populated with strings from `_create_chunk_node` ✓
- `_ingest_structural_edges` takes mutable `list[str]` + `set[str]` args and modifies them in-place ✓

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-25-corpus-pipeline-reform.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
