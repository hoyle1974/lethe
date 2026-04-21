<!--
SYNC IMPACT REPORT
==================
Version change: [TEMPLATE] → 1.0.0
Modified principles: N/A (initial authoring from template)
Added sections:
  - Core Principles (5 principles)
  - Development Workflow
  - Governance
Removed sections: N/A
Templates updated:
  - .specify/templates/plan-template.md ✅ (Constitution Check gate already present; principles referenced)
  - .specify/templates/spec-template.md ✅ (no principle-driven mandatory sections needed beyond existing)
  - .specify/templates/tasks-template.md ✅ (task structure aligns with principles; paths use lethe/ layout)
Deferred TODOs: None
-->

# Lethe Constitution

## Core Principles

### I. Async-First

All I/O in Lethe MUST be expressed as async/await. FastAPI route handlers, graph
operations, Firestore reads/writes, and LLM/embedder calls MUST be coroutines.
Blocking calls (e.g., `requests`, synchronous file I/O) inside an async context are
forbidden. Use `asyncio`-native libraries or run blocking work in a thread executor.

**Rationale**: The system is built on FastAPI + uvloop; mixing sync and async I/O
defeats the concurrency model and risks event-loop stalls under load.

### II. Dependency Injection via app.state

All infrastructure dependencies — Firestore client (`db`), LLM (`llm`), and embedder
(`embedder`) — MUST be initialised once in the FastAPI `lifespan` context and accessed
exclusively through `app.state` or the `deps.py` dependency-injection layer. Business
logic (graph, models, routers) MUST NOT import infra singletons directly or construct
them inline.

**Rationale**: Centralised wiring in `lifespan` makes the dependency graph explicit,
allows easy swapping of backends, and is the only way tests can substitute mocks
without monkey-patching import chains.

### III. GCP Test Isolation (NON-NEGOTIABLE)

Unit tests MUST stub all GCP-backed modules (Vertex AI / `vertexai`, Firestore) at the
`sys.modules` level in `tests/conftest.py` before any Lethe module imports them. No
test MUST make a live network call to GCP. Fixtures MUST use `MockEmbedder` and
`MockLLM` (or equivalent in-process fakes) for all LLM and embedding operations.
`pytest-asyncio` MUST run in `asyncio_mode = auto`.

**Rationale**: GCP credentials are unavailable in CI, and live calls make tests slow,
flaky, and environment-dependent. Module-level stubs are the only safe interception
point given how `vertexai` is imported.

### IV. Graph Semantics

The knowledge store MUST express all domain knowledge as typed `Node` and `Edge`
entities (see `lethe/models/node.py`). Edges MUST carry an explicit `predicate` string
(subject–predicate–object triple). Free-form relationship storage without a typed
predicate is forbidden. Node `node_type` MUST come from the defined type vocabulary;
ad-hoc string types require a constitution amendment.

**Rationale**: Consistent SPO triples are the foundation for traversal, contradiction
detection, and canonical-map resolution. Untyped edges break graph queries silently.

### V. Style Discipline

All Python code MUST:

- Pass `ruff check` (rules E, F, I — pycodestyle, pyflakes, isort) with zero errors.
- Be formatted by `ruff format` (line length 100) before every commit.
- Be executed via the project virtual environment (`.venv/bin/python`, `.venv/bin/pytest`,
  `.venv/bin/ruff`) — never system Python.

CI MUST reject commits that fail either check.

**Rationale**: Uniform formatting eliminates style noise in diffs; import sorting
prevents merge conflicts; using the venv guarantees reproducible dependency resolution.

## Development Workflow

**Running tests**:

```bash
.venv/bin/pytest tests/
```

**Linting and formatting** (run both before every commit):

```bash
.venv/bin/ruff check --fix .
.venv/bin/ruff format .
```

**Starting the server locally**:

```bash
.venv/bin/uvicorn lethe.main:app --reload
```

**Project layout**:

```text
lethe/
├── main.py          # FastAPI app + lifespan wiring
├── deps.py          # Dependency-injection accessors
├── config.py        # Settings (Pydantic/env-based)
├── constants.py     # Shared constants
├── types.py         # Shared type aliases
├── models/          # Pydantic domain models (Node, Edge, …)
├── graph/           # Graph algorithms (ingest, traverse, search, …)
├── infra/           # GCP adapters (Firestore, Gemini LLM, Embedder)
└── routers/         # FastAPI route handlers

tests/
├── conftest.py      # GCP stubs + shared fixtures
└── test_*.py        # Unit tests per module
```

## Governance

This constitution supersedes all other coding-practice documents for the Lethe project.
Amendments require:

1. A clear description of the principle being added, modified, or removed.
2. A version bump following semantic versioning:
   - **MAJOR**: principle removal or backward-incompatible redefinition.
   - **MINOR**: new principle or materially expanded guidance.
   - **PATCH**: clarifications, wording, or typo fixes.
3. Updated `LAST_AMENDED_DATE` and `CONSTITUTION_VERSION`.
4. All plan, spec, and tasks templates reviewed for alignment.

All PRs and code reviews MUST verify compliance with Principles I–V before merge.
Complexity that violates a principle MUST be justified in `plan.md` under
"Complexity Tracking" with a specific rationale for why a simpler approach was
rejected.

**Version**: 1.0.0 | **Ratified**: 2026-04-16 | **Last Amended**: 2026-04-16
