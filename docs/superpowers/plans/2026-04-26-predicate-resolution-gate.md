# Predicate Resolution Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before appending a LLM-proposed `NEW:` predicate to the canonical map, evaluate whether an existing predicate already covers the relationship — only adding to the ontology when the relationship is genuinely novel.

**Architecture:** A new `resolve_new_predicate` function in `lethe/graph/predicate_resolution.py` calls the LLM with the proposed predicate, its triple context, and all existing predicates. It returns either an existing predicate name (redirect) or the proposed name (approve). `_process_triple` in `ingest.py` gates the `append_predicate` call through this resolver — the edge is always written, but the canonical map only grows on a confirmed novel predicate. Fails open: any LLM error returns the proposed predicate so ingestion never stalls.

**Tech Stack:** Python 3.14, `LLMDispatcher` protocol (Gemini 2.5 Flash), Jinja2 prompt templates, pytest-asyncio

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `lethe/prompts/predicate_resolution.txt` | LLM system prompt for the resolution decision |
| Create | `lethe/graph/predicate_resolution.py` | `resolve_new_predicate()` — LLM call + response parsing |
| Modify | `lethe/constants.py` | Add `LLM_MAX_TOKENS_PREDICATE_RESOLUTION` |
| Modify | `lethe/graph/ingest.py` | Gate `append_predicate` through resolver in `_process_triple` |
| Create | `tests/test_predicate_resolution.py` | Unit tests for resolver logic |

---

## Task 1: Prompt file + token constant

**Files:**
- Create: `lethe/prompts/predicate_resolution.txt`
- Modify: `lethe/constants.py`

- [ ] **Step 1: Add the token constant to `lethe/constants.py`**

Open `lethe/constants.py` and add after the existing `LLM_MAX_TOKENS_*` block:

```python
LLM_MAX_TOKENS_PREDICATE_RESOLUTION = 256
```

- [ ] **Step 2: Write the prompt file**

Create `lethe/prompts/predicate_resolution.txt` with this exact content:

```
You are a knowledge graph ontology guardian. A new predicate has been proposed during graph ingestion.

Your job: decide whether an existing predicate already captures this relationship, or whether this is genuinely novel and should be added to the ontology.

## Proposed Predicate
{{ proposed }}

## Relationship Context
{{ subject }} --[{{ proposed }}]--> {{ object }}

## Existing Predicates
{{ existing_predicates }}

## Decision Rules
1. If any existing predicate captures the same semantic relationship — even with different wording — prefer it. For example, "employed_by" and "works_at" are the same; choose "works_at".
2. Only approve a new predicate if no existing one fits and the relationship is meaningfully distinct.
3. Direction matters: "reports_to" and "manages" describe the same connection from opposite ends — they are not the same predicate.
4. Do not approve predicates that are too generic to be useful (e.g. "related_to" already exists for that purpose).

## Output Format
If an existing predicate fits, reply with exactly:
EXISTING: <predicate_name>

If this is genuinely novel, reply with exactly:
NEW: approved

Reply with exactly one line. No explanation.
```

- [ ] **Step 3: Verify both files are in place**

```bash
cat lethe/prompts/predicate_resolution.txt
grep "LLM_MAX_TOKENS_PREDICATE_RESOLUTION" lethe/constants.py
```

Expected: prompt text prints cleanly; grep finds the constant.

- [ ] **Step 4: Commit**

```bash
git add lethe/prompts/predicate_resolution.txt lethe/constants.py
git commit -m "feat: add predicate resolution prompt and token constant"
```

---

## Task 2: `resolve_new_predicate` implementation

**Files:**
- Create: `lethe/graph/predicate_resolution.py`

- [ ] **Step 1: Write the failing test first** (in `tests/test_predicate_resolution.py`)

```python
import pytest
from tests.conftest import MockLLM
from lethe.graph.predicate_resolution import resolve_new_predicate

EXISTING = ["works_at", "lives_in", "knows", "related_to"]

@pytest.mark.asyncio
async def test_returns_existing_when_llm_matches():
    llm = MockLLM("EXISTING: works_at")
    result = await resolve_new_predicate(
        llm=llm,
        proposed="employed_by",
        triple_subject="Alice",
        triple_object="Anthropic",
        existing=EXISTING,
    )
    assert result == "works_at"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./.venv/bin/pytest tests/test_predicate_resolution.py::test_returns_existing_when_llm_matches -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `predicate_resolution` doesn't exist yet.

- [ ] **Step 3: Create `lethe/graph/predicate_resolution.py`**

The pattern in this codebase is: static string as `system_prompt`, rendered Jinja template as `user_prompt`. `GeminiLLM._generate` passes `user_prompt` directly to Gemini's `contents` field — an empty string there would fail.

```python
from __future__ import annotations

import logging
import os

from jinja2 import BaseLoader, Environment

from lethe.constants import LLM_MAX_TOKENS_PREDICATE_RESOLUTION
from lethe.infra.llm import LLMDispatcher, LLMRequest

log = logging.getLogger(__name__)

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
_RESOLUTION_TEMPLATE = None

RESOLUTION_SYSTEM = "You are a knowledge graph ontology guardian. Follow the output format exactly."


def _get_resolution_template():
    global _RESOLUTION_TEMPLATE
    if _RESOLUTION_TEMPLATE is None:
        path = os.path.join(_PROMPT_DIR, "predicate_resolution.txt")
        with open(path) as f:
            source = f.read()
        _RESOLUTION_TEMPLATE = Environment(loader=BaseLoader()).from_string(source)
    return _RESOLUTION_TEMPLATE


def _render_prompt(proposed: str, subject: str, object_: str, existing: list[str]) -> str:
    return _get_resolution_template().render(
        proposed=proposed,
        subject=subject,
        object=object_,
        existing_predicates=", ".join(existing),
    )


def _parse_response(text: str, existing: list[str], proposed: str) -> str:
    """Return the resolved predicate. Falls back to proposed on any parse failure."""
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if line.upper().startswith("EXISTING:"):
        candidate = line.split(":", 1)[1].strip().lower()
        if candidate in existing:
            return candidate
        log.warning(
            "predicate_resolution: LLM returned unknown existing predicate %r — using proposed %r",
            candidate,
            proposed,
        )
        return proposed
    return proposed


async def resolve_new_predicate(
    llm: LLMDispatcher,
    proposed: str,
    triple_subject: str,
    triple_object: str,
    existing: list[str],
) -> str:
    """
    Ask the LLM whether `proposed` maps to an existing predicate or is genuinely novel.

    Returns an existing predicate name if the LLM redirects, otherwise returns `proposed`.
    Falls back to `proposed` on any error so ingestion never stalls.
    """
    if not existing:
        return proposed
    try:
        user_prompt = _render_prompt(proposed, triple_subject, triple_object, existing)
        response = await llm.dispatch(
            LLMRequest(
                system_prompt=RESOLUTION_SYSTEM,
                user_prompt=user_prompt,
                max_tokens=LLM_MAX_TOKENS_PREDICATE_RESOLUTION,
            )
        )
        return _parse_response(response, existing, proposed)
    except Exception as exc:
        log.warning("predicate_resolution: LLM error, using proposed predicate: %s", exc)
        return proposed
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./.venv/bin/pytest tests/test_predicate_resolution.py::test_returns_existing_when_llm_matches -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lethe/graph/predicate_resolution.py tests/test_predicate_resolution.py
git commit -m "feat: add resolve_new_predicate with existing-predicate redirect"
```

---

## Task 3: Full unit test suite for `resolve_new_predicate`

**Files:**
- Modify: `tests/test_predicate_resolution.py`

- [ ] **Step 1: Add all remaining tests**

Replace the file content with the full suite:

```python
import pytest
from tests.conftest import MockLLM
from lethe.graph.predicate_resolution import resolve_new_predicate, _parse_response

EXISTING = ["works_at", "lives_in", "knows", "related_to"]


@pytest.mark.asyncio
async def test_returns_existing_when_llm_matches():
    llm = MockLLM("EXISTING: works_at")
    result = await resolve_new_predicate(
        llm=llm,
        proposed="employed_by",
        triple_subject="Alice",
        triple_object="Anthropic",
        existing=EXISTING,
    )
    assert result == "works_at"


@pytest.mark.asyncio
async def test_returns_proposed_when_llm_approves_novel():
    llm = MockLLM("NEW: approved")
    result = await resolve_new_predicate(
        llm=llm,
        proposed="has_child",
        triple_subject="Alice",
        triple_object="Bob",
        existing=EXISTING,
    )
    assert result == "has_child"


@pytest.mark.asyncio
async def test_falls_back_to_proposed_on_llm_error():
    class FailingLLM:
        async def dispatch(self, req):
            raise RuntimeError("LLM unavailable")

    result = await resolve_new_predicate(
        llm=FailingLLM(),
        proposed="mentors",
        triple_subject="Alice",
        triple_object="Bob",
        existing=EXISTING,
    )
    assert result == "mentors"


@pytest.mark.asyncio
async def test_rejects_hallucinated_existing_predicate():
    # LLM returns a predicate not in the existing list — must fall back to proposed
    llm = MockLLM("EXISTING: invented_predicate")
    result = await resolve_new_predicate(
        llm=llm,
        proposed="mentors",
        triple_subject="Alice",
        triple_object="Bob",
        existing=EXISTING,
    )
    assert result == "mentors"


@pytest.mark.asyncio
async def test_returns_proposed_when_existing_list_empty():
    llm = MockLLM("EXISTING: works_at")
    result = await resolve_new_predicate(
        llm=llm,
        proposed="mentors",
        triple_subject="Alice",
        triple_object="Bob",
        existing=[],
    )
    assert result == "mentors"


def test_parse_response_existing():
    assert _parse_response("EXISTING: works_at", ["works_at", "knows"], "employed_by") == "works_at"


def test_parse_response_novel():
    assert _parse_response("NEW: approved", ["works_at"], "has_child") == "has_child"


def test_parse_response_hallucinated():
    assert _parse_response("EXISTING: fake_pred", ["works_at"], "mentors") == "mentors"


def test_parse_response_empty():
    assert _parse_response("", ["works_at"], "mentors") == "mentors"


def test_parse_response_case_insensitive():
    assert _parse_response("existing: works_at", ["works_at"], "employed_by") == "works_at"
```

- [ ] **Step 2: Run the full suite**

```bash
./.venv/bin/pytest tests/test_predicate_resolution.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 3: Lint**

```bash
./.venv/bin/ruff check --fix lethe/graph/predicate_resolution.py tests/test_predicate_resolution.py
./.venv/bin/ruff format lethe/graph/predicate_resolution.py tests/test_predicate_resolution.py
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add tests/test_predicate_resolution.py lethe/graph/predicate_resolution.py
git commit -m "test: full unit suite for resolve_new_predicate"
```

---

## Task 4: Wire resolver into `_process_triple`

**Files:**
- Modify: `lethe/graph/ingest.py:154-229`

- [ ] **Step 1: Update the import block in `lethe/graph/ingest.py`**

Add the import alongside the existing canonical_map import (line 21):

```python
from lethe.graph.canonical_map import CanonicalMap, append_predicate
from lethe.graph.predicate_resolution import resolve_new_predicate
```

- [ ] **Step 2: Replace the `is_new_predicate` block in `_process_triple`**

The current block (lines 168–172) reads:

```python
    predicate = triple.canonical_predicate
    if triple.is_new_predicate:
        await append_predicate(db, predicate)
        if predicate not in canonical_map.allowed_predicates:
            canonical_map.allowed_predicates.append(predicate)
```

Replace it with:

```python
    predicate = triple.canonical_predicate
    if triple.is_new_predicate:
        predicate = await resolve_new_predicate(
            llm=llm,
            proposed=predicate,
            triple_subject=triple.subject,
            triple_object=triple.object,
            existing=canonical_map.allowed_predicates,
        )
        is_still_new = predicate == triple.canonical_predicate
        if is_still_new:
            await append_predicate(db, predicate)
            if predicate not in canonical_map.allowed_predicates:
                canonical_map.allowed_predicates.append(predicate)
        else:
            log.info(
                "predicate_resolution: redirected %r → %r",
                triple.canonical_predicate,
                predicate,
            )
```

- [ ] **Step 3: Run the existing ingest tests to confirm nothing is broken**

```bash
./.venv/bin/pytest tests/test_routers.py tests/test_extraction.py tests/test_canonical_map.py -v
```

Expected: all PASS. (The existing tests use `MockLLM` which returns `"status: none"` by default; the resolver is only reached when `triple.is_new_predicate` is True.)

- [ ] **Step 4: Lint**

```bash
./.venv/bin/ruff check --fix lethe/graph/ingest.py
./.venv/bin/ruff format lethe/graph/ingest.py
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add lethe/graph/ingest.py
git commit -m "feat: gate new-predicate append through resolve_new_predicate"
```

---

## Task 5: Integration test for the gated flow

**Files:**
- Modify: `tests/test_predicate_resolution.py`

This test exercises the full `_process_triple` path with a NEW: predicate that the resolver redirects to an existing one, confirming the edge is written with the existing predicate and `append_predicate` is never called.

- [ ] **Step 1: Add the integration test**

Append to `tests/test_predicate_resolution.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch, call
from lethe.graph.extraction import RefineryTriple
from lethe.graph.ingest import _process_triple
from lethe.graph.canonical_map import CanonicalMap


@pytest.mark.asyncio
async def test_process_triple_redirects_new_predicate_to_existing():
    """When resolver maps NEW:employed_by → works_at, the edge uses works_at and append_predicate is not called."""
    triple = RefineryTriple(
        subject="Alice",
        predicate="NEW:employed_by",
        object="Anthropic",
        subject_type="person",
        object_type="generic",
    )
    assert triple.is_new_predicate is True
    assert triple.canonical_predicate == "employed_by"

    canonical_map = CanonicalMap()  # includes "works_at" in defaults

    # Resolver LLM returns the existing predicate
    resolver_llm = MockLLM("EXISTING: works_at")

    # Stub out Firestore and embedder — we only care about predicate routing
    mock_db = MagicMock()
    mock_config = MagicMock()
    mock_config.lethe_collection = "nodes"
    mock_config.lethe_relationships_collection = "relationships"

    with (
        patch("lethe.graph.ingest.append_predicate", new_callable=AsyncMock) as mock_append,
        patch("lethe.graph.ingest._resolve_term", new_callable=AsyncMock) as mock_resolve,
        patch("lethe.graph.ingest._get_or_create_entity_node", new_callable=AsyncMock) as mock_entity,
        patch("lethe.graph.ingest.create_relationship_node", new_callable=AsyncMock) as mock_rel,
    ):
        mock_resolve.return_value = {"text": "Alice", "existing_uuid": None, "resolved_type": "person"}
        fake_node = MagicMock()
        fake_node.uuid = "entity_abc"
        fake_node.content = "Alice"
        mock_entity.return_value = (False, fake_node)
        mock_rel.return_value = "rel_xyz"

        await _process_triple(
            db=mock_db,
            embedder=MagicMock(),
            llm=resolver_llm,
            config=mock_config,
            triple=triple,
            entry_uuid="entry_001",
            ts="2026-01-01T00:00:00+00:00",
            user_id="global",
            nodes_created=[],
            nodes_updated=[],
            relationships_created=[],
            canonical_map=canonical_map,
        )

    # append_predicate must NOT have been called — existing predicate was used
    mock_append.assert_not_called()

    # create_relationship_node must have been called with "works_at", not "employed_by"
    _, kwargs = mock_rel.call_args
    assert kwargs["predicate"] == "works_at"

    # canonical_map must not have grown
    assert "employed_by" not in canonical_map.allowed_predicates


@pytest.mark.asyncio
async def test_process_triple_appends_genuinely_novel_predicate():
    """When resolver approves NEW:mentors, append_predicate is called and map grows."""
    triple = RefineryTriple(
        subject="Alice",
        predicate="NEW:mentors",
        object="Bob",
        subject_type="person",
        object_type="person",
    )

    canonical_map = CanonicalMap()
    assert "mentors" not in canonical_map.allowed_predicates

    resolver_llm = MockLLM("NEW: approved")

    mock_db = MagicMock()
    mock_config = MagicMock()
    mock_config.lethe_collection = "nodes"
    mock_config.lethe_relationships_collection = "relationships"

    with (
        patch("lethe.graph.ingest.append_predicate", new_callable=AsyncMock) as mock_append,
        patch("lethe.graph.ingest._resolve_term", new_callable=AsyncMock) as mock_resolve,
        patch("lethe.graph.ingest._get_or_create_entity_node", new_callable=AsyncMock) as mock_entity,
        patch("lethe.graph.ingest.create_relationship_node", new_callable=AsyncMock) as mock_rel,
    ):
        mock_resolve.return_value = {"text": "Alice", "existing_uuid": None, "resolved_type": "person"}
        fake_node = MagicMock()
        fake_node.uuid = "entity_abc"
        fake_node.content = "Alice"
        mock_entity.return_value = (False, fake_node)
        mock_rel.return_value = "rel_xyz"

        await _process_triple(
            db=mock_db,
            embedder=MagicMock(),
            llm=resolver_llm,
            config=mock_config,
            triple=triple,
            entry_uuid="entry_002",
            ts="2026-01-01T00:00:00+00:00",
            user_id="global",
            nodes_created=[],
            nodes_updated=[],
            relationships_created=[],
            canonical_map=canonical_map,
        )

    mock_append.assert_called_once_with(mock_db, "mentors")
    assert "mentors" in canonical_map.allowed_predicates

    _, kwargs = mock_rel.call_args
    assert kwargs["predicate"] == "mentors"
```

- [ ] **Step 2: Run the full test file**

```bash
./.venv/bin/pytest tests/test_predicate_resolution.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 3: Run the full test suite to confirm no regressions**

```bash
./.venv/bin/pytest -v
```

Expected: all tests PASS.

- [ ] **Step 4: Lint**

```bash
./.venv/bin/ruff check --fix tests/test_predicate_resolution.py
./.venv/bin/ruff format tests/test_predicate_resolution.py
```

- [ ] **Step 5: Final commit**

```bash
git add tests/test_predicate_resolution.py
git commit -m "test: integration tests for predicate resolution gate in _process_triple"
```
