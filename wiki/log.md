# Wiki Log

Append-only record of all wiki updates. Format: `YYYY-MM-DD: [page] description`

---

2026-04-24: [all] Initial wiki created — architecture, api, data-model, algorithms, decisions, index
2026-04-24: [algorithms] Added §6a source log enrichment — fetch_source_logs() wired into summarize pipeline
2026-04-25: [algorithms] Added §9 corpus ingestion pipeline, chunking strategies, traceability chain, triple cap note
2026-04-25: [api.md] Added POST /v1/ingest/corpus endpoint documentation
2026-04-25: [architecture] GeminiEmbedder migrated from vertexai.language_models (deprecated) to google.genai async client; vertexai stubs removed from conftest
2026-04-25: [algorithms] corpus.py now logs per-file start, chunk count, and per-chunk progress
