# TODO

## Graph Layer (`lethe/graph/`)

- [x] `consolidate.py`: Remove dead redundant slice — loop already enforces bound via `break`, final `return lines[:_MAX_STATEMENTS]` was unreachable
- [x] `traverse.py`: Move `FieldFilter` import from inside function body to module level (was re-importing on every call)
- [x] `extraction.py`: Add missing return type annotation on `_get_refinery_template` (`-> Template`)
- [x] `extraction.py`: Add missing return type annotation on `RefineryTriple.__post_init__` (`-> None`)
- [x] `ensure_node.py`: Add missing `firestore.AsyncCollectionReference` annotation on `collection` param in `_find_nearest_by_type`
- [x] `ingest.py`: Add full parameter and return type annotations to `_process_triple`, `_node_exists`, `_resolve_term`, `_get_or_create_entity_node`

## Infra / Models / Core (`lethe/infra/`, `lethe/models/`, core files)

- [x] `models/node.py`: Replace all 9 `Optional[X]` annotations with `X | None` across `Node`, `Edge`, `IngestRequest`, `SearchRequest`, `GraphExpandRequest`, `GraphSummarizeResponse`
- [x] `infra/gemini.py`: Replace bare silent `except Exception: pass` with `except Exception as exc: log.debug(...)` — was swallowing Gemini SDK errors invisibly
- [x] `models/node.py`: Fix `GraphExpandRequest.debug: bool = True` — debug output was on by default in production; changed to `False`

## Routers (`lethe/routers/`)

- [x] `ingest.py`: Fix POST `/v1/ingest` returning 200 instead of 201
- [x] `admin.py`: Fix POST `/v1/admin/backfill` returning 200 instead of 201
- [x] `admin.py`: Fix POST `/v1/admin/consolidate` returning 200 instead of 201
- [x] `entries.py`: Remove unused `Optional` import; switch `Optional[str]` to `str | None`
- [x] `nodes.py`: Remove unused `Optional` import; switch `Optional[str]` query params to `str | None`
- [x] `ingest.py`: Add missing `from __future__ import annotations` for consistency with all other router files

## Tests (`tests/`)

- [x] `test_serialization.py`: Remove duplicate `test_doc_to_node_strips_vector_distance` — identical test already existed in `test_search.py`
- [x] `test_consolidate.py`: Rename test to `test_post_consolidate_returns_201_with_statements`; update assertion from 200 → 201
- [x] `test_routers.py`: Update 3 status code assertions from 200 → 201 to match corrected POST endpoints
- [x] `test_node_models.py`: Update `GraphExpandRequest` debug assertion from `True` → `False`
