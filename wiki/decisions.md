# Architectural Decisions

## ADR-001: Firestore as sole persistence backend

**Decision**: Use Google Cloud Firestore (async) as the only storage backend.
**Rationale**: Native vector search support (Firestore vector index) avoids a separate vector DB. Integrates directly with Cloud Run + GCP auth. Schema-flexible for evolving node types.
**Consequence**: No SQL, no alternative backends. Firestore-level security rules are out of scope; isolation is enforced at query level via `user_id`.

---

## ADR-002: Gemini (Vertex AI) for LLM and embeddings

**Decision**: Use Gemini via Vertex AI (`gemini-2.5-flash` default) for all LLM calls and `text-embedding-005` for embeddings.
**Rationale**: Single GCP dependency. Vertex AI handles auth via ADC. Gemini 2.5 Flash has 1M token context and good structured output compliance.
**Consequence**: No OpenAI, no Anthropic. Switching LLMs requires implementing the `LLMDispatcher` protocol in `lethe/infra/llm.py`.

---

## ADR-003: No application-level authentication

**Decision**: Lethe performs no auth. `user_id` is caller-supplied. Access control is via Cloud Run IAM.
**Rationale**: Lethe is a backend service, not user-facing. Callers are trusted services with IAM tokens.
**Consequence**: Any caller with IAM access can read/write any user's data by supplying an arbitrary `user_id`.

---

## ADR-004: SELF token for first-person resolution

**Decision**: The LLM extraction prompt instructs the model to use the literal string `"SELF"` when a triple subject/object refers to the submitting user. The ingest pipeline resolves `"SELF"` to a deterministic per-user UUID via `stable_self_id(user_id)`.
**Rationale**: Avoids the LLM hallucinating usernames. Creates a single stable self-node per user regardless of how the user refers to themselves.
**Consequence**: Only one self-node per user. No support for multiple personas or identity aliases.

---

## ADR-005: Deterministic document IDs for entity deduplication

**Decision**: Entity nodes use `stable_entity_doc_id(node_type, name)` — a deterministic hash of `(node_type, lowercase_name)` — as their Firestore document ID.
**Rationale**: Enables cheap existence checks and idempotent writes without full-text search. Prevents duplicates from concurrent ingestion.
**Consequence**: Two entities with the same name and type are always the same node, regardless of context. Fine-grained disambiguation (e.g., two people named "Alice") is not supported.

---

## ADR-006: Tombstoning over deletion

**Decision**: Superseded nodes and edges are tombstoned (`weight = 0.0`) rather than deleted.
**Rationale**: Preserves provenance and history. Deletion in Firestore is cheap but irreversible; tombstoning allows recovery. Tombstoned records are filtered at query time.
**Consequence**: Firestore grows unbounded. Periodic cleanup (not yet implemented) would be required for large deployments.

---

## ADR-007: Flat predicate vocabulary in canonical map

**Decision**: Predicates are a flat list in an in-memory `CanonicalMap` (backed by Firestore). New predicates are added at runtime via `NEW:` prefix in LLM output.
**Rationale**: Schema-agnostic design. Avoids hardcoding ontology while keeping predicates normalized (lowercase snake_case).
**Consequence**: The predicate list grows over time. No hierarchical or typed predicate relationships.

---

## ADR-008: Always-200 on ingest

**Decision**: `POST /v1/ingest` always returns HTTP 200. LLM extraction errors are logged and skipped; the log node is always stored.
**Rationale**: Ingest is best-effort. Losing the log entry on an LLM error is worse than returning a partial result. Callers should not need to retry on transient LLM failures.
**Consequence**: Callers cannot distinguish "no triples extracted" from "LLM errored" without reading logs. `nodes_created` and `relationships_created` being empty is the signal.
