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
