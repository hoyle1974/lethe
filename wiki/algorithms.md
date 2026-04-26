# Algorithms

## 1. Ingest Pipeline (`lethe/graph/ingest.py::run_ingest`)

Steps executed for every `POST /v1/ingest`:

1. **Store log node** — embed text (Vertex AI `RETRIEVAL_DOCUMENT` task), write to Firestore `nodes` collection with `node_type="log"`, `weight=0.3`
2. **Extract triples** — call `extract_triples()` → Gemini with refinery prompt → parse `(status, [RefineryTriple])`
3. **If no triples**: return early with just `entry_uuid`
4. **For each triple**:
   a. If `is_new_predicate`: normalize predicate, append to Firestore canonical map + in-memory list
   b. `_resolve_term(subject)` + `_resolve_term(object)` — resolves SELF token, rejects placeholders, looks up internal IDs
   c. If either term resolves to None: drop triple, keep log entry
   d. `_get_or_create_entity_node()` for subject + object → returns `(existed: bool, Node)`
   e. `create_relationship_node()` → creates/updates edge in `relationships` collection
5. Return `IngestResponse` with created/updated lists

### Term Resolution Rules
- `"SELF"` → `{ text: "Me", existing_uuid: stable_self_id(user_id), resolved_type: "person" }`
- Placeholder terms (`"unknown"`, `"none"`, `"null"`, `"n/a"`, `"na"`, `"unspecified"`, `"generic"`) → None (triple dropped)
- Internal generated IDs (detected by `is_generated_id()`) → look up in Firestore; return None if not found or content is empty
- All others → pass through as text

---

## 2. Triple Extraction (`lethe/graph/extraction.py`)

- Prompt template: `lethe/prompts/refinery.txt` (Jinja2)
- Template receives: `node_types`, `allowed_predicates`, `text`, `owner_name`
- LLM output format (key/value):
  ```
  Status: found | none
  Triples:
  subject | predicate | object
  subject | predicate | object | subject_type | object_type
  ```
- New predicates prefixed with `NEW:` → `RefineryTriple.is_new_predicate=True`, normalized via `normalized_predicate()`
- `max_tokens`: `LLM_MAX_TOKENS_EXTRACTION = 32768`

---

## 3. Fact Collision Detection (`lethe/graph/collision.py`)

When an entity node with the same `stable_entity_doc_id` already exists:
- LLM call: present `new_fact` and `existing_fact`
- Prompt: `lethe/prompts/collision.txt`
- Returns `"update"` (overwrite content) or `"insert"` (create new node)
- Fallback on any error: `"insert"` (safe default)
- Controlled by `LETHE_COLLISION_DETECTION` env var; if disabled → always `"insert"`
- `max_tokens`: `LLM_MAX_TOKENS_FACT_COLLISION = 16`

---

## 4. Temporal Decay Scoring

Applied during search to rank results by recency. Formula:
```
decayed_score = base_score * exp(-ln(2) * age_days / half_life_days)
```
Half-lives:
- Log nodes: 30 days
- Entity nodes: 365 days
- Edges: 90 days

Combined with observation reinforcement: nodes referenced by more log entries score higher.

---

## 5. BFS Graph Traversal (`lethe/graph/traverse.py::graph_expand`)

```
graph_expand(seed_ids, query, hops, limit_per_edge, user_id, self_seed_neighbor_floor=40)
```

1. Embed `query` if provided (Vertex AI `RETRIEVAL_QUERY` task) → `query_vector`
2. Fetch seed nodes → initial frontier (non-log, weight > 0)
3. For each hop (up to `hops`):
   a. Gather edges for all frontier nodes (concurrent, semaphore=10): both outgoing (`subject_uuid`) and incoming (`object_uuid`)
   b. Collect candidate neighbor IDs; track SELF-node neighbors separately
   c. Fetch candidate nodes in batches of `TRAVERSE_BATCH_SIZE = 100`
   d. Filter: keep only non-log, weight > 0 nodes
   e. `prune_frontier_by_similarity(candidates, query_vector, limit_per_edge)`:
      - Score = `cosine_sim * 0.7 + (obs_count / max_obs) * 0.3`
      - Keep top-k by score
   f. `apply_self_seed_neighbor_floor(...)`: at hop 0, if SELF is in frontier, guarantee at least `self_seed_neighbor_floor` of SELF's direct neighbors survive pruning
   g. Add pruned frontier to `all_nodes`
4. Return `GraphExpandResponse(nodes=all_nodes, edges=all_edges)`

**Tombstone exclusion**: nodes with `weight == 0.0` are excluded (`_is_alive()` check); edges with `weight <= 0.0` are skipped.

**Source filter**: When `source_filter` is set, candidates are pre-filtered before `prune_frontier_by_similarity`. A candidate passes if `node.source is None` (shared entity) or `node.source == source_filter`. Seed nodes are filtered the same way. Use this to scope expansion to a single corpus while keeping shared entity nodes reachable.

---

## 6. Graph Summarization (two-pass, `lethe/routers/graph.py`)

1. **Pass 1**: `graph_expand()` from seeds → draft summary with LLM → LLM also emits gap-filling queries
2. **Pass 2**: for each gap query, run search → expand seed nodes → merge with pass 1 graph
3. **Final**: LLM generates structured summary from merged graph
4. **Retry**: if summary < 100 chars, retry once with explicit re-prompt
5. **Query mode detection**: ≤2 words → broad profile; `?` or question words → Q&A; else → free-form

---

## 6a. Source Log Enrichment (`lethe/graph/source_fetch.py`)

Called in the summarize pipeline after BFS expansion, before the final LLM pass.

```
fetch_source_logs(entity_nodes, db, config, max_per_node=2, max_total=30)
```

1. For each entity node in the expanded graph, take the last `max_per_node` entries from `journal_entry_ids` (most recent first — IDs are appended in insertion order)
2. Deduplicate across all entities; cap at `max_total` total log IDs
3. Batch-fetch from Firestore nodes collection
4. Return `entity_uuid → [log_node, ...]`

Result is passed to `GraphExpandResponse.to_markdown()` as `source_logs`. The markdown renders each entity's source log content inline:

```
- **person** `abc12345` [SEED]: Jack Strohm (metadata={})
  [source] "Started building Lethe last month, it's a graph-based memory system..."
  [source] "Working on the summarization pipeline today..."
```

This gives the final summarization LLM both the structured fact (compressed, precise) and the original prose (expressive, contextual).

Constants: `SOURCE_LOGS_MAX_PER_NODE = 2`, `SOURCE_LOGS_MAX_TOTAL = 30`, `SOURCE_LOG_SNIPPET_LENGTH = 250`

---

## 7. Memory Consolidation (`lethe/graph/consolidate.py`)

1. Fetch up to `CONSOLIDATION_LOG_QUERY_LIMIT = 50` recent log nodes for user
2. LLM call: distil into up to 3 core factual statements (`max_tokens = 1024`)
3. Re-ingest each statement via `run_ingest()` with `domain="core_memory"`
4. Returns `{ statements, ingest_results }`

---

## 8. Relationship Supersede Check (`lethe/graph/ensure_node.py`)

When a new edge has the same `(subject_uuid, predicate, object_type)` as an existing edge:
- LLM call: does new edge supersede the old?
- `max_tokens`: `LLM_MAX_TOKENS_RELATIONSHIP_SUPERSEDES = 64`
- If superseded: set old edge `weight = 0.0` (tombstone)
- Candidate limit: `RELATIONSHIP_SUPERSEDE_CANDIDATE_LIMIT = 10`

---

## 9. Corpus Ingestion (`lethe/graph/corpus.py::run_corpus_ingest`)

Entry point: `POST /v1/ingest/corpus`.

### Hub-and-Spoke Model

Chunks are stored as vector-indexed `node_type="chunk"` nodes only — no SPO triple extraction per chunk. Triple extraction runs exactly once per document on a generated LLM summary. Code files additionally receive a deterministic structural edges pass with no LLM involvement.

### Idempotency

Corpus and document nodes use **stable deterministic IDs** (SHA-1 of type+corpus_id+filename), so re-submitting the same `corpus_id` + `filename` always targets the same Firestore document. Each document node stores a `content_hash` (SHA-256 of the full text) in its `metadata`. On re-ingestion:
- **Unchanged** (`content_hash` matches) — document and its chunks are skipped entirely; existing chunk IDs are returned.
- **Changed** — old chunk nodes are tombstoned (`weight=0.0`), then the full pipeline re-runs for that document.
- **New** — full pipeline runs, `contains` edge created.

The corpus hub node is always upserted (content updated to reflect current file list).

Stable ID helpers: `stable_document_id(corpus_id, filename)` → `"doc_" + sha1(...)`, `stable_corpus_node_id(corpus_id)` → `"corpus_" + sha1(...)`.

Chunk nodes store `document_id` as a **top-level Firestore field** (in addition to JSON metadata) so they can be queried efficiently for tombstoning. A composite index on `(document_id, user_id)` covers these queries (`firestore.indexes.json`).

### Parallelism

Ingestion uses a two-phase `asyncio.gather` model:

- **Phase 1 (classify)** — corpus node upsert + all `_upsert_document_node` calls run concurrently. These are fast (one Firestore `get` + maybe one embed per doc; no LLM). Each document resolves to `(doc_id, is_new, is_changed)`.
- **Phase 2 (pipeline)** — each new/changed document runs `_process_document_pipeline` concurrently, bounded by `asyncio.Semaphore(CORPUS_INGEST_CONCURRENCY=5)` to stay within Gemini rate limits. Unchanged docs skip the pipeline and just fetch their existing chunk IDs. All results are merged after `gather` completes.

Steps:

1. **Upsert corpus node** — stable ID `corpus_<sha1>`. If new: `set()`. If existing: `update()` content+filenames. Node type `"corpus"`, `weight=1.0`, `source=corpus_id`.

Then, for each document (Phase 2, runs up to 5 concurrently):

2. **Upsert document node** — stable ID `doc_<sha1>`. Fetch existing; compare `content_hash`. If unchanged: skip to step 9 (reuse chunk IDs). If changed: `update()` in place, tombstone old chunks (step 3). If new: `set()`.
3. **Tombstone old chunks** (changed docs only) — query by `document_id + user_id`, set `weight=0.0`.
4. **Add `contains` edge** (new docs only) — `corpus_node --[contains]--> document_node`. Structural, `llm=None`.
5. **Summarize document** — `summarize_document(llm, text, filename)` → Gemini generates 3–5 entity-dense sentences capped at `DOCUMENT_SUMMARY_CHAR_LIMIT = 50 000` chars. Uses `lethe/prompts/document_summary.txt`. Max tokens: `LLM_MAX_TOKENS_DOCUMENT_SUMMARY = 512`.
6. **SPO extraction on summary** — call `run_ingest(summary)` once per document. Creates one `node_type="log"` summary node + entity/relationship nodes from triples. Tagged `metadata={"is_summary": True, "document_id": ..., "filename": ...}`.
7. **Chunk document** — `chunk_document(text, filename, chunk_size)` in `lethe/graph/chunk.py` dispatches to:
   - **Code** (`.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.java`, `.go`, `.rs`, `.c`, `.cpp`, `.h`, `.cs`, `.rb`, `.swift`, `.kt`): splits on top-level `def`/`class`/`async def` lines; prepends file preamble (imports) to each chunk; falls back to prose if no top-level defs found. Oversized blocks (>chunk_size×2 words) are split as prose with preamble re-injected into every sub-chunk.
   - **Prose** (all other extensions): splits on `\n\n`, accumulates up to `chunk_size` words, carries 1 trailing paragraph as overlap into the next chunk.
8. **Store chunk nodes** — each chunk written as `node_type="chunk"`, `weight=0.4`, vector-indexed. `document_id` stored both in JSON metadata and as a top-level field. No LLM extraction.
9. **Structural edges for code files** — if `detect_chunk_strategy(filename) == "code"`, `_ingest_structural_edges()` calls `extract_structural_triples(text, filename)` and writes entity + relationship nodes without LLM:
   - `.py` files: stdlib `ast` parser extracts `(module, imports, dep)`, `(module, defines, fn)`, `(ClassName, has_method, fn)` triples.
   - Other code types: regex-based import extraction.
   - Entity node types: `module` for subjects/import targets, `function` for defines/has_method targets.
   - `llm=None` passed to `ensure_node` and `create_relationship_node` (no collision detection, no supersede check).
10. **Aggregate** — set-based deduplication of `nodes_created`, `nodes_updated`, `relationships_created` across all documents.

### Traceability chain

```
corpus node  (node_type="corpus", id=stable_corpus_node_id, source=corpus_id)
  └── document node  (node_type="document", id=stable_document_id, metadata={content_hash}, source=corpus_id)  [via contains edge]
        └── summary log node  (node_type="log", metadata={"is_summary": True, "document_id": ...})
              └── entity/relationship nodes  (source=corpus_id)
chunk nodes  (node_type="chunk", document_id=<top-level field>, metadata={"document_id": ..., "chunk_index": N}, source=corpus_id)
structural entity nodes  (node_type="module"|"function", source=corpus_id)  [code files only]
structural edges  (predicate="imports"|"defines"|"has_method")  [code files only]
```

### Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `NODE_TYPE_CORPUS` | `"corpus"` | Node type for the corpus hub node |
| `CORPUS_NODE_WEIGHT` | `1.0` | Default weight for corpus hub node |
| `NODE_TYPE_CHUNK` | `"chunk"` | Node type for raw chunk nodes |
| `CHUNK_NODE_WEIGHT` | `0.4` | Default weight for chunk nodes |
| `LLM_MAX_TOKENS_DOCUMENT_SUMMARY` | `512` | Max tokens for document summary |
| `DOCUMENT_SUMMARY_CHAR_LIMIT` | `50 000` | Max chars sent to summarizer |
| `DEFAULT_CHUNK_SIZE` | `600` | Words per chunk (configurable per request) |
