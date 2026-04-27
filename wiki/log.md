# Wiki Log

Append-only record of all wiki updates. Format: `YYYY-MM-DD: [page] description`

---

2026-04-24: [all] Initial wiki created — architecture, api, data-model, algorithms, decisions, index
2026-04-24: [algorithms] Added §6a source log enrichment — fetch_source_logs() wired into summarize pipeline
2026-04-25: [algorithms] Added §9 corpus ingestion pipeline, chunking strategies, traceability chain, triple cap note
2026-04-25: [api.md] Added POST /v1/ingest/corpus endpoint documentation
2026-04-25: [algorithms] §9 updated for hub-and-spoke corpus model — summary-only SPO, chunk nodes, deterministic code graph
2026-04-25: [api.md] Updated POST /v1/ingest/corpus response to include chunk_ids; updated description for hub-and-spoke
2026-04-25: [architecture] GeminiEmbedder migrated from vertexai.language_models (deprecated) to google.genai async client; vertexai stubs removed from conftest
2026-04-25: [algorithms] corpus.py now logs per-file start, chunk count, and per-chunk progress
2026-04-25: [algorithms] §5 BFS updated — source_filter pre-filtering for namespace scoping
2026-04-25: [api.md] Added source_filter field to POST /v1/graph/expand and /v1/graph/summarize
2026-04-26: [algorithms] §9 corpus ingestion — added corpus hub node (node_type="corpus") as searchable anchor; contains edges to document nodes
2026-04-26: [api.md] POST /v1/ingest/corpus response now includes corpus_node_id
2026-04-26: [algorithms] §9 corpus ingestion — idempotent upsert via stable SHA-1 IDs and SHA-256 content hash; chunk tombstoning on update; document_id as top-level chunk field for queryability
2026-04-26: [architecture] GeminiLLM._generate wrapped in asyncio.wait_for(timeout=90s) to prevent hanging API calls
2026-04-26: [algorithms] §9 corpus ingestion — two-phase parallel model: asyncio.gather for classify, gather+Semaphore(5) for LLM pipeline
2026-04-26: [algorithms] §9 corpus ingestion — LLM throttling moved to _RateLimitedLLM wrapper (max 3 concurrent); all pipeline work runs fully parallel; timeout increased to 180s
2026-04-26: [algorithms] Added §10 predicate resolution gate — LLM evaluation before new predicates enter canonical map; §1 step 4a updated to reference it
2026-04-26: [algorithms] §2 extraction strategy updated — "key relationships only", cap 20 triples (changed from "extract all, cap 50" in f83af05)
2026-04-26: [data-model] Added corpus, document, chunk node type variants with weights and stable ID helpers; canonical map description updated to mention predicate resolution gate; added corpus/chunk/predicate-resolution constants
2026-04-26: [architecture] Router table: ingest.py now shows POST /v1/ingest/corpus; added corpus ingest data flow section
2026-04-27: [api.md, architecture] Corpus ingest redesigned — returns 202 immediately; fan-out mode (LETHE_SERVICE_URL) spawns one Cloud Run call per document via POST /v1/ingest/corpus/document; in-process mode for local/small corpora
2026-04-26: [algorithms] Corpus changes: has_chunk edges (document→chunk, concurrent); no temporal decay for corpus/document/chunk nodes (half-life=∞); chunk rendering in to_markdown() via ## Source Chunks section
2026-04-26: [algorithms] Corpus structural bridge: has_summary (document→log), next_chunk (chunk chain), mentioned_in (entity→chunk via regex); entity fetch via db.get_all(); traceability chain updated
2026-04-26: [algorithms] Corpus completion signalling: pipeline_done_at field written per document; ingest_ts in 202 response; status endpoint POST /v1/ingest/corpus/{corpus_id}/status; corpus.sh polls with spinner
2026-04-27: [api.md, architecture] fix: status endpoint used snap.get() which raises KeyError for absent fields; changed to snap.to_dict().get(); corpus.sh poll loop now checks HTTP status before parsing JSON to survive transient 500s
2026-04-27: [algorithms] Phase 2 gather uses return_exceptions=True — one document pipeline failure is logged and skipped instead of aborting the full corpus ingest
2026-04-27: [architecture] GeminiEmbedder.embed/embed_batch wrapped in asyncio.wait_for(timeout=60s) — stalled TCP connections to the embed API no longer hang structural-edges loops indefinitely
2026-04-27: [api.md, architecture] corpus.sh now passes repo-relative paths (e.g. lethe/graph/ingest.py) instead of basenames — files with the same name in different directories no longer produce the same stable_document_id and inflate total in the status check; status endpoint also deduplicates document_ids defensively
2026-04-27: [api.md] Added GET /v1/stats endpoint — node counts by type, edge counts by predicate; added scripts/stats.sh which renders a combined terminal report including canonical vocabulary
