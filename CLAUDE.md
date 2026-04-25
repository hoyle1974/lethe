# Claude Project Instructions

## Python Execution Rule

- Always run Python commands via the project virtual environment.
- Do not invoke system `python`, `pip`, or `pytest`.
- Required command forms:
  - `./.venv/bin/python ...`
  - `./.venv/bin/pip ...`
  - `./.venv/bin/pytest ...`

## Code Style

- Use Python best practices and formatting.
- Always use Ruff for formatting and linting: `./.venv/bin/ruff format .` and `./.venv/bin/ruff check --fix .`
- Run both before committing any Python code.

## Active Technologies
- Python 3.14 + FastAPI, uvicorn, uvloop, google-cloud-firestore, google-cloud-aiplatform (001-knowledge-graph-spec)
- Google Cloud Firestore (two collections: `nodes` and `relationships`; one config (001-knowledge-graph-spec)

## Recent Changes
- 001-knowledge-graph-spec: Added Python 3.14 + FastAPI, uvicorn, uvloop, google-cloud-firestore, google-cloud-aiplatform

## Wiki

A `wiki/` directory at the project root contains structured markdown files for LLM context. These files are owned and maintained by the LLM — keep them accurate and up-to-date as the codebase evolves.

### When to Read
- **Session start**: Read `wiki/index.md` before any non-trivial code work to orient yourself.
- **Targeted reads**: Pull specific pages when working in that area:
  - Touching `lethe/graph/` → read `wiki/algorithms.md`
  - Touching `lethe/routers/` or `lethe/models/` → read `wiki/api.md`
  - Touching `lethe/infra/firestore.py` or `lethe/models/node.py` → read `wiki/data-model.md`
  - Understanding a design choice → read `wiki/decisions.md`
  - Unsure where to start → read `wiki/architecture.md`

### When to Update
After any significant code change, update the relevant wiki page(s) to reflect the new state. Then append one line to `wiki/log.md`:

```
YYYY-MM-DD: [page] description of what changed
```

Examples:
```
2026-04-24: [algorithms] Updated BFS pruning to use new scoring formula
2026-04-24: [api.md] Added POST /v1/entries endpoint
2026-04-24: [decisions] Added ADR-009 for new caching strategy
```

### Lint (periodically)
- Verify `index.md` summaries still match page content
- Check no page exists that is absent from `index.md`
- Flag and resolve contradictions between pages
- Remove stale content that no longer matches the code
