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
