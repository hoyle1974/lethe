# TODO

## Critical Bugs

- [x] **`ensure_node.py:252-264`** — Remove duplicate embedding call on collision update; use already-computed `vector` instead of re-calling `embedder.embed` with identical inputs
- [x] **`ingest.py:307-328`** — Guard `_get_or_create_entity_node` against calling `.update()` on a non-existent Firestore document (deleted between resolve and write)
- [x] **`ensure_node.py:224,315`** — Replace `NODE_TYPE_ENTITY` with `DEFAULT_DOMAIN` for the `domain` field; node type identifier is being used as a domain value, breaking domain-filtered queries
- [x] **`infra/gemini.py:92-94`** — Exception path returns `"status: none\ntriples:\n"`, indistinguishable from a legitimate empty response; raise or return a typed error result
- [ ] **`routers/entries.py:46,56` / `routers/nodes.py:44,56`** — Client-side type filtering silently truncates results when matching docs are sparse; add composite Firestore index on `(user_id, node_type)` and filter server-side

## Security

- [x] **`routers/graph.py:159-199`** — `req.query` is interpolated directly into the LLM system prompt; wrap in a delimited block and enforce max length on `GraphExpandRequest`

## Unused / Dead Code

- [x] **`constants.py:11`** — Delete unused `NODE_TYPE_RELATIONSHIP` constant
- [x] **`types.py:10-13`** — Delete unused `CoreNodeType` type alias, or apply it to `node_type` field annotations
- [x] **`extraction.py:68`** — Delete `resolve_pronoun` (never called in production; LLM prompt handles it) along with its tests, or wire it into `parse_refinery_output`
- [x] **`contradiction.py:49-59`** — Remove dead `new_rel_id` parameter from `tombstone_relationship`, or implement `"superseded_by": new_rel_id` as intended
- [x] **`prompts/refinery.txt`** — Add `{{ owner_name }}` token to rule 9 in template, or remove the `owner_name` parameter from `build_refinery_prompt` and `extract_triples` entirely
- [x] **`routers/admin.py:10-11,65`** — Remove unnecessary `EMBEDDING_TASK_RETRIEVAL_DOCUMENT` import and explicit argument (it is already the default)

## Design / Performance

- [ ] **`deps.py:23-25`** — Cache canonical map in `app.state` during lifespan instead of re-fetching Firestore on every request; invalidate on mutation
- [ ] **`routers/admin.py:58-69`** — Backfill endpoint: use `embed_batch`, add a request timeout, and consider a background task with status polling instead of sequential per-doc embed+write
- [x] **`graph/search.py:169`** — Fix dead branch: `max(limit * 5, limit)` always equals `limit * 5`; simplify to `min(limit * 5, _SEARCH_POOL_MAX)` or `min(max(limit * 5, 1), _SEARCH_POOL_MAX)`
- [x] **`ensure_node.py` / `ingest.py`** — Consolidate `_ENTITY_DOC_ID_RE` and `_GENERATED_ID_RE` into a single shared regex in `lethe/graph/ids.py`; the narrower pattern misses `rel_<sha1>` IDs

## FastAPI / Code Structure

- [x] **`routers/ingest.py:15-17`** — Add type annotations to dependencies (`db: firestore.AsyncClient`, `embedder: Embedder`, `llm: LLMDispatcher`) to match all other routers
- [x] **`deps.py:7-20`** — Make all dependency functions consistently plain `def` (not mixed `def`/`async def`); pure `app.state` attribute access does not need to be async
- [ ] **`ensure_node.py` / `search.py`** — Move `doc_to_node` and `doc_to_edge` to `lethe/graph/serialization.py`; update all import sites to avoid the transitive re-export through `search.py`

## Tests

- [x] **`test_contradiction.py`** — Delete duplicate `FakeLLM`; use existing `MockLLM` from conftest
- [x] **`test_gemini.py` / `test_gemini_llm.py`** — Merge into a single `test_gemini.py`
- [x] **`test_models.py`** — Rename to `test_protocols.py` (it tests mock protocol conformance, not Pydantic models)
- [x] **`contradiction.py`** — Add tests for `tombstone_relationship` (exists and not-exists paths)
- [ ] **`consolidate.py` / `routers/admin.py`** — Add tests for `run_consolidation` and `POST /v1/admin/consolidate`
- [ ] **`routers/admin.py`** — Add test for `POST /v1/admin/backfill` covering docs with and without embeddings
- [ ] **`test_routers.py`** — Fix test setup to match production path: `get_canonical_map` calls `load_canonical_map(db)` live; test sets `app.state.canonical_map` which production never sets

## Configuration / Dependencies

- [x] **`requirements.txt`** — Pin `google-genai` with a lower-bound version
- [x] **`requirements.txt`** — Add explicit `uvloop>=0.19.0` dependency (currently pulled in incidentally via `uvicorn[standard]`, not guaranteed)
- [x] **`stubs/google/`** — Delete empty namespace stub packages; the real `google-cloud-firestore` is installed and these do nothing
- [x] **`Dockerfile`** — Add `HEALTHCHECK CMD curl -f http://localhost:8080/v1/health || exit 1`
- [x] **Project root**  — Add `.dockerignore` excluding `.venv/`, `tests/`, `stubs/`, `specs/`, `docs/`, `*.md`
