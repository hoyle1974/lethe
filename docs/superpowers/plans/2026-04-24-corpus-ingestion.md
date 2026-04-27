# Corpus Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /v1/ingest/corpus` — accept multiple related documents (e.g. a codebase), store each as a raw document node with full text, chunk intelligently, and ingest all chunks into the knowledge graph tagged with a shared corpus ID.

**Architecture:** Each submitted document becomes a `document` node in Firestore that preserves the full original text verbatim. That document is then chunked (paragraph-aware for prose, function/class-aware for code) and each chunk flows through the existing `run_ingest` pipeline, with `source=corpus_id` and `metadata` carrying the parent `document_id` and `chunk_index`. All nodes and relationships are tagged with `corpus_id` via the `source` field. The extraction triple cap is also raised globally from 15 → 50 so large chunks don't silently drop relationships.

**Tech Stack:** Python 3.14, FastAPI, Pydantic v2, google-cloud-firestore, Jinja2 (existing), pytest + AsyncMock (existing test patterns in `tests/conftest.py` and `tests/test_routers.py`)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `lethe/prompts/refinery.txt` | Raise triple cap 15 → 50 |
| Modify | `lethe/constants.py` | Add `NODE_TYPE_DOCUMENT`, `DEFAULT_CHUNK_SIZE`, `DOCUMENT_NODE_WEIGHT` |
| **Create** | `lethe/graph/chunk.py` | All chunking logic — prose (paragraph) and code (def/class) |
| Modify | `lethe/graph/ingest.py` | Add `metadata: dict \| None` param to `run_ingest`; store in Firestore |
| **Create** | `lethe/graph/corpus.py` | `create_document_node` + `run_corpus_ingest` orchestration |
| Modify | `lethe/models/node.py` | Add `DocumentItem`, `CorpusIngestRequest`, `CorpusIngestResponse` |
| Modify | `lethe/routers/ingest.py` | Add `POST /v1/ingest/corpus` handler |
| **Create** | `tests/test_chunk.py` | Unit tests for all chunking functions |
| Modify | `tests/test_routers.py` | Add corpus endpoint integration tests |
| Modify | `wiki/algorithms.md` | Document corpus ingest pipeline |
| Modify | `wiki/api.md` | Document new endpoint |
| Modify | `wiki/log.md` | Append change entry |

---

## Task 1: Raise the triple extraction cap

The refinery prompt currently says "Output up to 15 triples." This silently drops relationships in dense text. Raise it to 50 globally — `LLM_MAX_TOKENS_EXTRACTION = 32768` already provides plenty of output budget.

**Files:**
- Modify: `lethe/prompts/refinery.txt`

- [ ] **Step 1: Edit refinery.txt**

In `lethe/prompts/refinery.txt`, find line 5 of the Rules section:

```
5. EXTRACT ALL: Extract every distinct relationship in the text. Do not stop early.
   Output up to 15 triples. If no relationships exist, output: status: none
```

Change to:

```
5. EXTRACT ALL: Extract every distinct relationship in the text. Do not stop early.
   Output up to 50 triples. If no relationships exist, output: status: none
```

- [ ] **Step 2: Run existing extraction tests to confirm nothing is broken**

```bash
./.venv/bin/pytest tests/test_extraction.py -v
```

Expected: all tests pass (the cap is a prompt instruction, not parsed by Python).

- [ ] **Step 3: Commit**

```bash
git add lethe/prompts/refinery.txt
git commit -m "feat: raise triple extraction cap from 15 to 50"
```

---

## Task 2: Add constants

**Files:**
- Modify: `lethe/constants.py`

- [ ] **Step 1: Add three constants to `lethe/constants.py`**

After the existing `NODE_TYPE_ENTITY = "entity"` line, add:

```python
NODE_TYPE_DOCUMENT = "document"
```

After the `DEFAULT_LOG_WEIGHT = 0.3` line, add:

```python
DOCUMENT_NODE_WEIGHT = 1.0
DEFAULT_CHUNK_SIZE = 600
```

- [ ] **Step 2: Run full test suite to confirm no breakage**

```bash
./.venv/bin/pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add lethe/constants.py
git commit -m "feat: add NODE_TYPE_DOCUMENT, DOCUMENT_NODE_WEIGHT, DEFAULT_CHUNK_SIZE constants"
```

---

## Task 3: Chunking module (TDD)

This module is pure Python with no I/O — easy to test thoroughly.

**Files:**
- Create: `lethe/graph/chunk.py`
- Create: `tests/test_chunk.py`

- [ ] **Step 1: Write the failing tests in `tests/test_chunk.py`**

```python
from lethe.graph.chunk import chunk_code, chunk_document, chunk_text, detect_chunk_strategy


def test_detect_chunk_strategy_python():
    assert detect_chunk_strategy("main.py") == "code"


def test_detect_chunk_strategy_typescript():
    assert detect_chunk_strategy("app.ts") == "code"


def test_detect_chunk_strategy_javascript():
    assert detect_chunk_strategy("router.js") == "code"


def test_detect_chunk_strategy_prose_md():
    assert detect_chunk_strategy("README.md") == "prose"


def test_detect_chunk_strategy_prose_txt():
    assert detect_chunk_strategy("notes.txt") == "prose"


def test_detect_chunk_strategy_no_extension():
    assert detect_chunk_strategy("Makefile") == "prose"


def test_chunk_text_short_text_single_chunk():
    text = "Alice works at Acme.\n\nBob is her manager."
    chunks = chunk_text(text, chunk_size=100)
    assert len(chunks) == 1
    assert "Alice" in chunks[0]
    assert "Bob" in chunks[0]


def test_chunk_text_splits_large_text():
    # 4 paragraphs of 10 words each = 40 words total; chunk_size=15 forces splits
    paras = ["word " * 10 for _ in range(4)]
    text = "\n\n".join(paras)
    chunks = chunk_text(text, chunk_size=15)
    assert len(chunks) > 1


def test_chunk_text_overlap_carries_last_paragraph():
    # With overlap=1, the last paragraph of chunk N appears in chunk N+1
    para_a = "Alice is the founder and CEO of Acme Corporation based in New York."
    para_b = "Bob runs the engineering department and reports directly to Alice."
    para_c = "Carol joined as a new engineer working under Bob this quarter."
    text = f"{para_a}\n\n{para_b}\n\n{para_c}"
    chunks = chunk_text(text, chunk_size=12, overlap=1)
    # If split occurred, overlap means para_b appears in both chunks
    if len(chunks) > 1:
        assert para_b in chunks[0] or para_b in chunks[1]


def test_chunk_text_empty_string():
    chunks = chunk_text("", chunk_size=100)
    assert chunks == []


def test_chunk_text_whitespace_only():
    chunks = chunk_text("   \n\n  \n\n  ", chunk_size=100)
    assert chunks == []


def test_chunk_code_splits_on_top_level_defs():
    code = (
        "import os\n\n"
        "def foo():\n"
        "    return 1\n\n"
        "def bar():\n"
        "    return 2\n"
    )
    chunks = chunk_code(code)
    assert len(chunks) == 2
    assert "def foo" in chunks[0]
    assert "def bar" in chunks[1]


def test_chunk_code_splits_on_class():
    code = (
        "import sys\n\n"
        "class Foo:\n"
        "    pass\n\n"
        "class Bar:\n"
        "    pass\n"
    )
    chunks = chunk_code(code)
    assert len(chunks) == 2
    assert "class Foo" in chunks[0]
    assert "class Bar" in chunks[1]


def test_chunk_code_includes_preamble_in_each_chunk():
    code = (
        "import os\n"
        "import sys\n\n"
        "def foo():\n"
        "    return os.getcwd()\n\n"
        "def bar():\n"
        "    return sys.argv\n"
    )
    chunks = chunk_code(code)
    assert len(chunks) == 2
    assert "import os" in chunks[0]
    assert "import os" in chunks[1]


def test_chunk_code_async_def():
    code = (
        "async def foo():\n"
        "    pass\n\n"
        "async def bar():\n"
        "    pass\n"
    )
    chunks = chunk_code(code)
    assert len(chunks) == 2
    assert "async def foo" in chunks[0]
    assert "async def bar" in chunks[1]


def test_chunk_code_no_defs_falls_back_to_prose():
    text = "x = 1\ny = 2\nz = x + y"
    chunks = chunk_code(text, chunk_size=100)
    assert len(chunks) == 1
    assert "x = 1" in chunks[0]


def test_chunk_document_routes_py_to_code():
    code = "import os\n\ndef foo():\n    pass\n\ndef bar():\n    pass\n"
    chunks = chunk_document(code, filename="main.py", chunk_size=100)
    assert len(chunks) == 2


def test_chunk_document_routes_txt_to_prose():
    text = "Short text.\n\nAnother paragraph."
    chunks = chunk_document(text, filename="notes.txt", chunk_size=100)
    assert len(chunks) == 1


def test_chunk_document_no_filename_uses_prose():
    text = "Short text.\n\nAnother paragraph."
    chunks = chunk_document(text, filename="", chunk_size=100)
    assert len(chunks) == 1
```

- [ ] **Step 2: Run tests to verify they all fail**

```bash
./.venv/bin/pytest tests/test_chunk.py -v
```

Expected: `ModuleNotFoundError: No module named 'lethe.graph.chunk'`

- [ ] **Step 3: Create `lethe/graph/chunk.py`**

```python
from __future__ import annotations

import re
from pathlib import Path

_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
    ".c", ".cpp", ".h", ".cs", ".rb", ".swift", ".kt",
}

_TOP_LEVEL_DEF = re.compile(r"^(def |class |async def )", re.MULTILINE)


def detect_chunk_strategy(filename: str) -> str:
    """Return 'code' or 'prose' based on file extension."""
    ext = Path(filename).suffix.lower()
    return "code" if ext in _CODE_EXTENSIONS else "prose"


def chunk_text(text: str, chunk_size: int = 600, overlap: int = 1) -> list[str]:
    """Split prose into overlapping paragraph-boundary chunks.

    chunk_size is measured in words (approximate tokens).
    overlap is the number of trailing paragraphs carried into the next chunk.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    window: list[str] = []
    size = 0

    for para in paragraphs:
        words = len(para.split())
        if size + words > chunk_size and window:
            chunks.append("\n\n".join(window))
            window = window[-overlap:]
            size = sum(len(p.split()) for p in window)
        window.append(para)
        size += words

    if window:
        chunks.append("\n\n".join(window))

    return chunks


def chunk_code(text: str, chunk_size: int = 600) -> list[str]:
    """Split source code at top-level def/class/async def boundaries.

    Each chunk includes the file preamble (imports, module-level code before
    the first definition). Falls back to prose chunking if no definitions found.
    """
    lines = text.splitlines(keepends=True)

    preamble_lines: list[str] = []
    blocks: list[list[str]] = []
    current: list[str] = []
    found_def = False

    for line in lines:
        if _TOP_LEVEL_DEF.match(line):
            if current:
                if found_def:
                    blocks.append(current)
                else:
                    preamble_lines = current
            current = [line]
            found_def = True
        else:
            current.append(line)

    if current:
        if found_def:
            blocks.append(current)
        else:
            preamble_lines = current

    if not blocks:
        return chunk_text(text, chunk_size)

    preamble = "".join(preamble_lines).strip()
    chunks: list[str] = []

    for block in blocks:
        block_text = "".join(block).strip()
        content = f"{preamble}\n\n{block_text}" if preamble else block_text
        if len(content.split()) > chunk_size * 2:
            chunks.extend(chunk_text(content, chunk_size))
        else:
            chunks.append(content)

    return chunks


def chunk_document(text: str, filename: str = "", chunk_size: int = 600) -> list[str]:
    """Chunk a document using the appropriate strategy for its file type."""
    if detect_chunk_strategy(filename) == "code":
        return chunk_code(text, chunk_size)
    return chunk_text(text, chunk_size)
```

- [ ] **Step 4: Run tests to verify they all pass**

```bash
./.venv/bin/pytest tests/test_chunk.py -v
```

Expected: all 18 tests pass.

- [ ] **Step 5: Format and lint**

```bash
./.venv/bin/ruff format lethe/graph/chunk.py tests/test_chunk.py
./.venv/bin/ruff check --fix lethe/graph/chunk.py tests/test_chunk.py
```

- [ ] **Step 6: Commit**

```bash
git add lethe/graph/chunk.py tests/test_chunk.py
git commit -m "feat: add chunk module with prose and code chunking strategies"
```

---

## Task 4: Extend run_ingest with metadata param

The corpus pipeline needs to attach `document_id`, `chunk_index`, and `filename` to each chunk's log node so it's traceable back to the source document.

**Files:**
- Modify: `lethe/graph/ingest.py`

- [ ] **Step 1: Add `import json` at top of `lethe/graph/ingest.py`**

The file already has `import uuid` and others. Add `import json` alongside them:

```python
import json
import logging
import uuid
```

- [ ] **Step 2: Add `metadata` param to `run_ingest` signature**

Change the function signature (starting at line 47) from:

```python
async def run_ingest(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    canonical_map: CanonicalMap,
    text: str,
    domain: str = DEFAULT_DOMAIN,
    source: Optional[str] = None,
    user_id: str = DEFAULT_USER_ID,
    timestamp: Optional[str] = None,
) -> IngestResponse:
```

To:

```python
async def run_ingest(
    db: firestore.AsyncClient,
    embedder: Embedder,
    llm: LLMDispatcher,
    config: Config,
    canonical_map: CanonicalMap,
    text: str,
    domain: str = DEFAULT_DOMAIN,
    source: Optional[str] = None,
    user_id: str = DEFAULT_USER_ID,
    timestamp: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> IngestResponse:
```

- [ ] **Step 3: Use `metadata` when writing the log node**

In the Firestore `.set()` call inside `run_ingest`, change:

```python
            "metadata": "{}",
```

To:

```python
            "metadata": json.dumps(metadata) if metadata else "{}",
```

- [ ] **Step 4: Run full test suite**

```bash
./.venv/bin/pytest tests/ -v
```

Expected: all existing tests pass (the new param is optional with default `None`).

- [ ] **Step 5: Format and lint**

```bash
./.venv/bin/ruff format lethe/graph/ingest.py
./.venv/bin/ruff check --fix lethe/graph/ingest.py
```

- [ ] **Step 6: Commit**

```bash
git add lethe/graph/ingest.py
git commit -m "feat: add optional metadata param to run_ingest, stored on log node"
```

---

## Task 5: Add corpus models

**Files:**
- Modify: `lethe/models/node.py`

- [ ] **Step 1: Add three new models to `lethe/models/node.py`**

Add these imports to the top of `lethe/models/node.py` (after existing imports):

```python
from lethe.constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_DOMAIN,
    DEFAULT_RELATIONSHIP_WEIGHT,
    DEFAULT_USER_ID,
    SOURCE_LOG_SNIPPET_LENGTH,
)
```

Wait — `DEFAULT_CHUNK_SIZE` needs to be added to the existing import block. The file already imports from `lethe.constants`. Change the existing import to include `DEFAULT_CHUNK_SIZE`:

```python
from lethe.constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_DOMAIN,
    DEFAULT_RELATIONSHIP_WEIGHT,
    DEFAULT_USER_ID,
    SOURCE_LOG_SNIPPET_LENGTH,
)
```

Then append these three models at the bottom of the file:

```python
class DocumentItem(BaseModel):
    text: str
    filename: str


class CorpusIngestRequest(BaseModel):
    corpus_id: str | None = None
    documents: list[DocumentItem]
    user_id: str = DEFAULT_USER_ID
    domain: str = DEFAULT_DOMAIN
    chunk_size: int = DEFAULT_CHUNK_SIZE


class CorpusIngestResponse(BaseModel):
    corpus_id: str
    document_ids: list[str] = Field(default_factory=list)
    total_chunks: int = 0
    nodes_created: list[str] = Field(default_factory=list)
    nodes_updated: list[str] = Field(default_factory=list)
    relationships_created: list[str] = Field(default_factory=list)
```

- [ ] **Step 2: Run full test suite**

```bash
./.venv/bin/pytest tests/ -v
```

Expected: all existing tests pass.

- [ ] **Step 3: Format and lint**

```bash
./.venv/bin/ruff format lethe/models/node.py
./.venv/bin/ruff check --fix lethe/models/node.py
```

- [ ] **Step 4: Commit**

```bash
git add lethe/models/node.py
git commit -m "feat: add DocumentItem, CorpusIngestRequest, CorpusIngestResponse models"
```

---

## Task 6: Corpus ingestion logic

**Files:**
- Create: `lethe/graph/corpus.py`

No separate unit tests for corpus.py — the integration test in Task 7 covers the full flow. The individual pieces (`create_document_node`, `run_ingest`, `chunk_document`) are all tested independently.

- [ ] **Step 1: Create `lethe/graph/corpus.py`**

```python
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from google.cloud import firestore

from lethe.config import Config
from lethe.constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_DOMAIN,
    DEFAULT_USER_ID,
    DOCUMENT_NODE_WEIGHT,
    EMBEDDING_TASK_RETRIEVAL_DOCUMENT,
    NODE_TYPE_DOCUMENT,
)
from lethe.graph.canonical_map import CanonicalMap
from lethe.graph.chunk import chunk_document
from lethe.graph.ingest import run_ingest
from lethe.infra.embedder import Embedder
from lethe.infra.fs_helpers import Vector
from lethe.infra.llm import LLMDispatcher
from lethe.models.node import CorpusIngestResponse, DocumentItem

log = logging.getLogger(__name__)


async def _create_document_node(
    db: firestore.AsyncClient,
    embedder: Embedder,
    config: Config,
    text: str,
    filename: str,
    corpus_id: str,
    user_id: str,
    domain: str,
    ts: str,
) -> str:
    doc_id = str(uuid.uuid4())
    # Embed first 10 000 chars — document nodes are retrieved by similarity
    vector = await embedder.embed(text[:10_000], EMBEDDING_TASK_RETRIEVAL_DOCUMENT)
    metadata = json.dumps({"filename": filename, "corpus_id": corpus_id})
    await db.collection(config.lethe_collection).document(doc_id).set(
        {
            "node_type": NODE_TYPE_DOCUMENT,
            "content": text,
            "domain": domain,
            "weight": DOCUMENT_NODE_WEIGHT,
            "metadata": metadata,
            "embedding": Vector(vector),
            "user_id": user_id,
            "source": corpus_id,
            "created_at": ts,
            "updated_at": ts,
        }
    )
    log.info("corpus: created document node doc_id=%s filename=%r", doc_id, filename)
    return doc_id


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
    all_nodes_created: list[str] = []
    all_nodes_updated: list[str] = []
    all_relationships_created: list[str] = []
    total_chunks = 0

    for doc in documents:
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

        chunks = chunk_document(doc.text, doc.filename, chunk_size)
        log.info(
            "corpus: document doc_id=%s filename=%r split into %d chunks",
            doc_id, doc.filename, len(chunks),
        )

        for i, chunk_text in enumerate(chunks):
            result = await run_ingest(
                db=db,
                embedder=embedder,
                llm=llm,
                config=config,
                canonical_map=canonical_map,
                text=chunk_text,
                domain=domain,
                source=corpus_id,
                user_id=user_id,
                timestamp=ts,
                metadata={"document_id": doc_id, "chunk_index": i, "filename": doc.filename},
            )
            total_chunks += 1
            for n in result.nodes_created:
                if n not in all_nodes_created:
                    all_nodes_created.append(n)
            for n in result.nodes_updated:
                if n not in all_nodes_updated and n not in all_nodes_created:
                    all_nodes_updated.append(n)
            for r in result.relationships_created:
                if r not in all_relationships_created:
                    all_relationships_created.append(r)

    log.info(
        "corpus: complete corpus_id=%s documents=%d chunks=%d nodes_created=%d",
        corpus_id, len(document_ids), total_chunks, len(all_nodes_created),
    )
    return CorpusIngestResponse(
        corpus_id=corpus_id,
        document_ids=document_ids,
        total_chunks=total_chunks,
        nodes_created=all_nodes_created,
        nodes_updated=all_nodes_updated,
        relationships_created=all_relationships_created,
    )
```

- [ ] **Step 2: Format and lint**

```bash
./.venv/bin/ruff format lethe/graph/corpus.py
./.venv/bin/ruff check --fix lethe/graph/corpus.py
```

- [ ] **Step 3: Run full test suite**

```bash
./.venv/bin/pytest tests/ -v
```

Expected: all existing tests pass (corpus.py has no tests of its own yet).

- [ ] **Step 4: Commit**

```bash
git add lethe/graph/corpus.py
git commit -m "feat: add corpus ingestion logic with document node creation and chunk pipeline"
```

---

## Task 7: Corpus router endpoint + integration tests (TDD)

**Files:**
- Modify: `lethe/routers/ingest.py`
- Modify: `tests/test_routers.py`

- [ ] **Step 1: Write the failing tests — append to `tests/test_routers.py`**

Add at the end of `tests/test_routers.py`:

```python
def test_corpus_ingest_generates_corpus_id(mock_embedder, mock_llm):
    """Corpus endpoint returns a corpus_id when none is provided."""
    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
    mock_doc_ref.update = AsyncMock()
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.stream = AsyncMock(
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
    assert "corpus_id" in data
    assert isinstance(data["corpus_id"], str)
    assert len(data["corpus_id"]) > 0


def test_corpus_ingest_accepts_explicit_corpus_id(mock_embedder, mock_llm):
    """Provided corpus_id is preserved in the response."""
    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
    mock_doc_ref.update = AsyncMock()
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.stream = AsyncMock(
        return_value=_async_iter([])
    )
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={
            "corpus_id": "my-corpus-abc",
            "documents": [{"text": "Bob runs engineering.", "filename": "notes.txt"}],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["corpus_id"] == "my-corpus-abc"


def test_corpus_ingest_returns_document_ids(mock_embedder, mock_llm):
    """One document_id is returned per submitted document."""
    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
    mock_doc_ref.update = AsyncMock()
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.stream = AsyncMock(
        return_value=_async_iter([])
    )
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={
            "documents": [
                {"text": "Doc one content.", "filename": "a.txt"},
                {"text": "Doc two content.", "filename": "b.txt"},
            ]
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["document_ids"]) == 2
    assert data["total_chunks"] >= 2


def test_corpus_ingest_total_chunks_counted(mock_embedder, mock_llm):
    """total_chunks reflects at least one chunk per document."""
    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
    mock_doc_ref.update = AsyncMock()
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.stream = AsyncMock(
        return_value=_async_iter([])
    )
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={"documents": [{"text": "Some text.", "filename": "notes.txt"}]},
    )
    assert resp.status_code == 201
    assert resp.json()["total_chunks"] >= 1
```

- [ ] **Step 2: Run just the new tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_routers.py -k "corpus" -v
```

Expected: `404 Not Found` or `AttributeError` — endpoint doesn't exist yet.

- [ ] **Step 3: Add the corpus endpoint to `lethe/routers/ingest.py`**

Replace the entire contents of `lethe/routers/ingest.py` with:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends
from google.cloud import firestore

from lethe.config import Config
from lethe.deps import get_canonical_map, get_config, get_db, get_embedder, get_llm
from lethe.graph.canonical_map import CanonicalMap
from lethe.graph.corpus import run_corpus_ingest
from lethe.graph.ingest import run_ingest
from lethe.infra.embedder import Embedder
from lethe.infra.llm import LLMDispatcher
from lethe.models.node import CorpusIngestRequest, CorpusIngestResponse, IngestRequest, IngestResponse

router = APIRouter()


@router.post("/v1/ingest", response_model=IngestResponse, status_code=201)
async def ingest(
    req: IngestRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    llm: LLMDispatcher = Depends(get_llm),
    config: Config = Depends(get_config),
    canonical_map: CanonicalMap = Depends(get_canonical_map),
):
    ts = req.timestamp.isoformat() if req.timestamp else None
    return await run_ingest(
        db=db,
        embedder=embedder,
        llm=llm,
        config=config,
        canonical_map=canonical_map,
        text=req.text,
        domain=req.domain,
        source=req.source,
        user_id=req.user_id,
        timestamp=ts,
    )


@router.post("/v1/ingest/corpus", response_model=CorpusIngestResponse, status_code=201)
async def ingest_corpus(
    req: CorpusIngestRequest,
    db: firestore.AsyncClient = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
    llm: LLMDispatcher = Depends(get_llm),
    config: Config = Depends(get_config),
    canonical_map: CanonicalMap = Depends(get_canonical_map),
):
    return await run_corpus_ingest(
        db=db,
        embedder=embedder,
        llm=llm,
        config=config,
        canonical_map=canonical_map,
        documents=req.documents,
        corpus_id=req.corpus_id,
        user_id=req.user_id,
        domain=req.domain,
        chunk_size=req.chunk_size,
    )
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_routers.py -k "corpus" -v
```

Expected: all 4 corpus tests pass.

- [ ] **Step 5: Run the full test suite to verify no regressions**

```bash
./.venv/bin/pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Format and lint**

```bash
./.venv/bin/ruff format lethe/routers/ingest.py tests/test_routers.py
./.venv/bin/ruff check --fix lethe/routers/ingest.py tests/test_routers.py
```

- [ ] **Step 7: Commit**

```bash
git add lethe/routers/ingest.py tests/test_routers.py
git commit -m "feat: add POST /v1/ingest/corpus endpoint for multi-document corpus ingestion"
```

---

## Task 8: Update wiki

**Files:**
- Modify: `wiki/algorithms.md`
- Modify: `wiki/api.md`
- Modify: `wiki/log.md`

- [ ] **Step 1: Add corpus ingest section to `wiki/algorithms.md`**

Append after the existing Section 7 (Memory Consolidation):

```markdown
---

## 9. Corpus Ingestion (`lethe/graph/corpus.py::run_corpus_ingest`)

Entry point: `POST /v1/ingest/corpus`.

Steps for each document in the request:

1. **Create document node** — embed first 10 000 chars, write to Firestore with `node_type="document"`, `weight=1.0`, `source=corpus_id`, `metadata={"filename": ..., "corpus_id": ...}`
2. **Chunk the document** — `chunk_document(text, filename, chunk_size)` dispatches to:
   - **Code** (`.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.java`, `.go`, `.rs`, `.c`, `.cpp`, `.h`, `.cs`, `.rb`, `.swift`, `.kt`): splits on top-level `def`/`class`/`async def` lines; prepends file preamble (imports) to each chunk; falls back to prose if no top-level defs found
   - **Prose** (all other extensions): splits on `\n\n`, accumulates up to `chunk_size` words, carries 1 trailing paragraph as overlap into the next chunk
3. **Ingest each chunk** — calls `run_ingest()` with `source=corpus_id` and `metadata={"document_id": ..., "chunk_index": ..., "filename": ...}`; the full existing triple extraction pipeline runs per chunk
4. **Aggregate** — deduplicated lists of `nodes_created`, `nodes_updated`, `relationships_created` across all chunks and documents

### Traceability chain

```
document node  (node_type="document", content=full text, source=corpus_id)
  └── chunk log nodes  (node_type="log", source=corpus_id, metadata.document_id=doc_id)
        └── entity/relationship nodes  (source=corpus_id)
```

### Triple cap

The extraction prompt cap was raised from 15 → 50 triples per call (Task 1 of corpus implementation). `LLM_MAX_TOKENS_EXTRACTION = 32768` is the hard output ceiling.
```

- [ ] **Step 2: Add corpus endpoint to `wiki/api.md`**

After the `## POST /v1/ingest` section, insert:

```markdown
---

## POST /v1/ingest/corpus
Ingest a collection of related documents (e.g. a codebase) as a single corpus.
Stores each document as a raw `document` node preserving full original text, then
chunks and ingests each chunk through the standard triple extraction pipeline.
All nodes and edges are tagged `source=corpus_id`.

**Request** (`CorpusIngestRequest`):
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `corpus_id` | string | no | generated UUID | Stable ID for this corpus; re-use to append |
| `documents` | list[DocumentItem] | yes | — | Each item: `{ "text": "...", "filename": "..." }` |
| `user_id` | string | no | `"global"` | User scope |
| `domain` | string | no | `"general"` | Namespace |
| `chunk_size` | int | no | `600` | Approximate words per chunk |

`filename` controls chunking strategy: `.py`/`.js`/`.ts`/etc → code (function/class splits); everything else → prose (paragraph splits).

**Response** (`CorpusIngestResponse`):
```json
{
  "corpus_id": "uuid-or-caller-provided",
  "document_ids": ["doc-uuid-1", "doc-uuid-2"],
  "total_chunks": 42,
  "nodes_created": ["entity_abc", "..."],
  "nodes_updated": [],
  "relationships_created": ["rel_xyz", "..."]
}
```
```

- [ ] **Step 3: Append to `wiki/log.md`**

```
2026-04-24: [algorithms] Added §9 corpus ingestion pipeline, chunking strategies, traceability chain
2026-04-24: [api.md] Added POST /v1/ingest/corpus endpoint documentation
```

- [ ] **Step 4: Commit**

```bash
git add wiki/algorithms.md wiki/api.md wiki/log.md
git commit -m "docs: update wiki for corpus ingestion — algorithms §9 and api.md"
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|-------------|------|
| New `POST /v1/ingest/corpus` endpoint | Task 7 |
| Store original raw document as a node | Task 6 (`_create_document_node`, `node_type="document"`) |
| Paragraph-aware chunking for prose | Task 3 (`chunk_text`) |
| Code-aware chunking (function/class) for source files | Task 3 (`chunk_code`) |
| Tag all nodes/edges with corpus_id via `source` | Task 6 (`source=corpus_id` in `run_ingest` call) |
| Chunk metadata (document_id, chunk_index, filename) on log nodes | Task 4 (`metadata` param) + Task 6 |
| Multiple documents per request | Task 5 (`documents: list[DocumentItem]`) + Task 6 loop |
| Optional caller-provided `corpus_id` | Task 5 model + Task 6 (`corpus_id or str(uuid.uuid4())`) |
| Triple cap raised from 15 → 50 | Task 1 |

No gaps found.

### Placeholder scan

No TBDs, TODOs, or "similar to Task N" references. All code blocks are complete.

### Type consistency

- `DocumentItem` defined in Task 5, used in Task 6 and Task 7 — consistent
- `CorpusIngestRequest` defined in Task 5, used in Task 7 — consistent
- `CorpusIngestResponse` defined in Task 5, returned by `run_corpus_ingest` in Task 6, typed as `response_model` in Task 7 — consistent
- `run_corpus_ingest` signature in Task 6 matches import in Task 7 — consistent
- `metadata: Optional[dict]` added to `run_ingest` in Task 4; called with `metadata={...}` in Task 6 — consistent
- `_create_document_node` defined and called only within `corpus.py` — consistent
