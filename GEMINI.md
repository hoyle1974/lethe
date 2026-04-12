# Gemini Project Instructions

## Python Execution Rule

- Always use the repository virtual environment for Python tasks.
- Never use global/system `python`, `pip`, or `pytest`.
- Use:
  - `./.venv/bin/python ...`
  - `./.venv/bin/pip ...`
  - `./.venv/bin/pytest ...`

## Code Style

- Use Python best practices and formatting.
- Always use Ruff for formatting and linting: `./.venv/bin/ruff format .` and `./.venv/bin/ruff check --fix .`
- Run both before committing any Python code.
