# Lethe Agent Instructions

## Python Execution Rule

- Always use the project virtual environment for Python commands.
- Never use system-level `python`, `pip`, or `pytest` for this repository.
- Use:
  - `./.venv/bin/python`
  - `./.venv/bin/pip`
  - `./.venv/bin/pytest`

## Code Style

- Use Python best practices and formatting.
- Always use Ruff for formatting and linting: `./.venv/bin/ruff format .` and `./.venv/bin/ruff check --fix .`
- Run both before committing any Python code.
