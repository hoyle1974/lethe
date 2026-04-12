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
