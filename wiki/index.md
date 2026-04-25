# Wiki Index

LLM context files for the Lethe codebase. Read this first, then pull specific pages.

## System

| Page | Summary |
|------|---------|
| [architecture.md](architecture.md) | Tech stack (Python 3.14, FastAPI, Gemini, Firestore), startup sequence, router layout, data flow diagrams |
| [api.md](api.md) | All endpoints: method, path, request/response fields, defaults, and notes |
| [data-model.md](data-model.md) | Firestore Node + Edge schemas, weight defaults, half-lives, ID strategies, canonical map |
| [algorithms.md](algorithms.md) | Ingest pipeline, triple extraction, fact collision, temporal decay, BFS traversal, summarization, consolidation |
| [decisions.md](decisions.md) | ADRs: why Firestore, Gemini, no auth, SELF token, deterministic IDs, tombstoning, always-200 |

## Meta

| Page | Summary |
|------|---------|
| [log.md](log.md) | Append-only record of all wiki updates |

---

## Quick Navigation by Task

| Task | Pages to read |
|------|--------------|
| Adding/modifying an endpoint | api.md, architecture.md |
| Changing ingest logic | algorithms.md (§1 Ingest Pipeline), data-model.md |
| Changing traversal/expansion | algorithms.md (§5 BFS), data-model.md |
| Changing Firestore schema | data-model.md, algorithms.md |
| Understanding a design choice | decisions.md |
| Adding a new LLM call | algorithms.md, architecture.md (Infra Layer) |
