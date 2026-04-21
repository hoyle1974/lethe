# Quickstart: Lethe Knowledge Graph API

**Branch**: `001-knowledge-graph-spec` | **Date**: 2026-04-16

---

## Prerequisites

- Python 3.14 (via `pyenv` or system install)
- Google Cloud SDK (`gcloud`) authenticated to a project with Firestore and Vertex AI enabled
- A `.env` file at the repository root (see Configuration below)

---

## Installation

```bash
# Create and activate virtual environment
python3.14 -m venv .venv
source .venv/bin/activate

# Install dependencies
.venv/bin/pip install -e ".[dev]"
```

---

## Configuration

Create `.env` in the repository root:

```env
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
LETHE_COLLECTION=nodes
LETHE_RELATIONSHIPS_COLLECTION=relationships
LETHE_EMBEDDING_MODEL=text-embedding-005
LETHE_LLM_MODEL=gemini-2.5-flash
LETHE_COLLISION_DETECTION=true
LETHE_SIMILARITY_THRESHOLD=0.25
LETHE_ENTITY_THRESHOLD=0.15
LETHE_REGION=us-central1
LOG_LEVEL=info
```

Ensure GCP Application Default Credentials are configured:

```bash
gcloud auth application-default login
```

---

## Starting the Server

```bash
.venv/bin/uvicorn lethe.main:app --reload --host 0.0.0.0 --port 8000
```

Verify the server is running:

```bash
curl http://localhost:8000/v1/health
# → {"status":"ok"}
```

---

## First Ingest

```bash
curl -X POST http://localhost:8000/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Alice works at Acme Corp as a senior engineer and lives in Seattle.",
    "user_id": "alice"
  }'
```

Expected response:
```json
{
  "entry_uuid": "...",
  "nodes_created": ["entity_...", "entity_...", "entity_..."],
  "nodes_updated": [],
  "relationships_created": ["rel_...", "rel_..."]
}
```

---

## Search the Graph

```bash
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Where does Alice work?",
    "user_id": "alice",
    "limit": 10
  }'
```

---

## Expand the Graph Around a Node

Use a node UUID from a previous ingest or search response:

```bash
curl -X POST http://localhost:8000/v1/graph/expand \
  -H "Content-Type: application/json" \
  -d '{
    "seed_ids": ["entity_..."],
    "query": "Alice professional context",
    "hops": 2,
    "limit_per_edge": 20,
    "user_id": "alice"
  }'
```

---

## Summarise the Graph

```bash
curl -X POST http://localhost:8000/v1/graph/summarize \
  -H "Content-Type: application/json" \
  -d '{
    "seed_ids": ["entity_..."],
    "query": "Alice",
    "hops": 2,
    "limit_per_edge": 20,
    "user_id": "alice",
    "debug": false
  }'
```

---

## Memory Consolidation

After accumulating many log entries, distil core facts:

```bash
curl -X POST http://localhost:8000/v1/admin/consolidate \
  -H "Content-Type: application/json" \
  -d '{ "user_id": "alice" }'
```

---

## Running Tests

```bash
# All tests (no live GCP required — all GCP deps are stubbed)
.venv/bin/pytest tests/

# With verbose output
.venv/bin/pytest tests/ -v

# Single test file
.venv/bin/pytest tests/test_ingest_resolution.py -v
```

---

## Linting and Formatting

Run both before every commit:

```bash
.venv/bin/ruff check --fix .
.venv/bin/ruff format .
```

---

## Viewing the Canonical Vocabulary

```bash
curl http://localhost:8000/v1/node-types
```

Returns the current set of recognised node types and relationship predicates. New predicates
proposed by the LLM during ingestion are automatically added here.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `nodes_created: []` after ingest | LLM returned `status: none` | Check `LOG_LEVEL=debug` output for LLM response |
| Search returns 0 results | No embeddings in Firestore | Run `/v1/admin/backfill` then retry |
| 422 Unprocessable Entity | Missing required field | Ensure `text` (ingest) or `query` (search) provided |
| `GOOGLE_APPLICATION_CREDENTIALS` error | ADC not configured | Run `gcloud auth application-default login` |
| `GOOGLE_CLOUD_PROJECT` error | Missing env var | Add to `.env` file |
