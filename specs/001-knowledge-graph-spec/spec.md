# Feature Specification: Lethe Knowledge Graph API

**Feature Branch**: `001-knowledge-graph-spec`
**Created**: 2026-04-16
**Status**: Draft
**Input**: Reverse-engineered from existing `lethe/` codebase implementation

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ingest Free-Text Knowledge (Priority: P1)

A client submits a natural-language statement or journal entry. The system stores the raw
text as an episodic log, then uses an LLM to extract structured knowledge (subject–predicate–object
triples) from the text and persist them as nodes and relationships in the graph.

**Why this priority**: This is the entry point for all knowledge; without it, no graph data exists.

**Independent Test**: Submit a single sentence via `POST /v1/ingest`, verify response contains
`entry_uuid` and at least one of `nodes_created`, `nodes_updated`, or `relationships_created`.

**Acceptance Scenarios**:

1. **Given** a text containing at least one extractable fact, **When** `POST /v1/ingest` is called,
   **Then** the system stores an episodic log node and returns one or more node/relationship UUIDs.
2. **Given** a text where the LLM finds no extractable triples, **When** `POST /v1/ingest` is called,
   **Then** the system stores the log node and returns an `entry_uuid` with empty created/updated lists.
3. **Given** a text referencing "I" or "me", **When** ingested, **Then** the system resolves the
   first-person pronoun to the user's canonical self-node ("Me"), creating it if not yet present.
4. **Given** the same named entity is mentioned in a second ingestion, **When** processed,
   **Then** the system updates the existing entity node rather than creating a duplicate.
5. **Given** an LLM-proposed new predicate (prefixed `NEW:`), **When** processed,
   **Then** the predicate is normalised, added to the canonical predicate list, and used for the edge.

---

### User Story 2 - Semantic Search Over the Graph (Priority: P1)

A client submits a natural-language query and receives ranked nodes and edges from the graph that are
semantically relevant to the query. Results are filtered by user and optionally by node type or domain.
Relevance is adjusted by temporal decay (recent knowledge scores higher) and reinforcement (frequently
observed knowledge scores higher).

**Why this priority**: Core retrieval capability; without it the stored knowledge is inaccessible.

**Independent Test**: After ingesting at least one statement, call `POST /v1/search` with a related
query and verify the response contains at least one matching node or edge.

**Acceptance Scenarios**:

1. **Given** knowledge exists in the graph, **When** `POST /v1/search` is sent with a related query,
   **Then** the response includes semantically similar nodes and edges, ranked by adjusted relevance.
2. **Given** a `node_types` filter is supplied, **When** searching, **Then** only nodes of the
   requested types are returned; edges are unaffected by node_types.
3. **Given** a `min_significance` threshold, **When** searching, **Then** nodes and edges with
   weight below the threshold are excluded from results.
4. **Given** knowledge exists for two different users, **When** user A searches, **Then** only
   user A's knowledge is returned; user B's data is never exposed.
5. **Given** a `domain` filter is provided, **When** searching, **Then** only nodes and edges in
   that domain are returned.

---

### User Story 3 - Graph Neighbourhood Expansion (Priority: P2)

A client supplies one or more seed node UUIDs and receives the multi-hop subgraph around those
nodes — all connected nodes and the edges that link them. The expansion prunes candidate nodes
at each hop using a combined similarity + observation score so the returned graph is focused
and bounded in size.

**Why this priority**: Enables contextual retrieval ("what does the graph around this entity look like?")
which powers summarisation and downstream AI context assembly.

**Independent Test**: Call `POST /v1/graph/expand` with a known seed UUID, verify response contains
that node plus at least one neighbour node and at least one edge.

**Acceptance Scenarios**:

1. **Given** a valid seed UUID and `hops=2`, **When** `POST /v1/graph/expand` is called, **Then**
   the response contains nodes reachable within 2 relationship hops plus all connecting edges.
2. **Given** `limit_per_edge` is set, **When** expanding, **Then** each hop's frontier is pruned to
   at most `limit_per_edge` nodes (ranked by combined similarity and observation count).
3. **Given** the SELF node is a seed and `self_seed_neighbor_floor` > 0, **When** expanding hop 1,
   **Then** at least `self_seed_neighbor_floor` of SELF's direct neighbours are included even if
   they would otherwise be pruned.
4. **Given** a node with weight 0.0 (tombstoned), **When** encountered during expansion, **Then**
   it is excluded from the returned node set.
5. **Given** an optional `query` string is provided, **When** pruning the frontier, **Then** nodes
   are ranked by cosine similarity to the query vector combined with observation count.

---

### User Story 4 - Graph-Grounded Summarisation (Priority: P2)

A client submits a query and seed node UUIDs and receives a structured natural-language summary
grounded in the retrieved graph neighbourhood. The system runs a two-pass retrieval–augmentation
loop: expand the initial graph, draft a summary and identify gaps, retrieve additional context
to fill those gaps, then produce a final enriched summary.

**Why this priority**: This is the primary consumer-facing intelligence surface — it turns raw
graph data into actionable answers.

**Independent Test**: After sufficient ingestion, call `POST /v1/graph/summarize` with a broad
query and verify the response contains a non-empty `summary` string.

**Acceptance Scenarios**:

1. **Given** a question-form query (e.g., "Who is X?"), **When** `POST /v1/graph/summarize` is
   called, **Then** the summary is structured with Answer, Evidence, and Gaps sections.
2. **Given** a broad query (≤2 words), **When** summarising, **Then** the system generates a
   comprehensive profile covering all domains present in the graph (work, relationships, personal,
   open items).
3. **Given** the LLM draft summary is too short (< 100 characters), **When** detected, **Then**
   the system automatically retries with an explicit re-prompt before returning.
4. **Given** `debug=true` in the request, **When** the response is returned, **Then** it includes
   a `debug_reasoning` object with query mode flags, pass 1 / pass 2 node counts, and LLM thought queries.
5. **Given** the LLM identifies retrieval gap queries in pass 1, **When** those queries find additional
   nodes, **Then** a pass 2 expansion is performed and the resulting graph is merged with pass 1 before
   final summarisation.

---

### User Story 5 - Memory Consolidation (Priority: P3)

An operator triggers memory consolidation for a user. The system reads the user's recent episodic
log nodes, uses an LLM to distil up to 3 core factual statements, and re-ingests those statements
as structured `core_memory` domain knowledge. This promotes ephemeral logs into durable graph facts.

**Why this priority**: Maintenance operation that improves long-term knowledge quality; not needed for
initial operation.

**Independent Test**: After ingesting multiple log entries, call `POST /v1/admin/consolidate`,
verify response contains at least one `statements` entry and a corresponding `ingest_results` entry.

**Acceptance Scenarios**:

1. **Given** a user has recent log entries, **When** `POST /v1/admin/consolidate` is called, **Then**
   the system returns 1–3 synthesised factual statements and their ingest results.
2. **Given** a user has no log entries, **When** consolidation is triggered, **Then** the system
   returns empty `statements` and `ingest_results` lists without error.
3. **Given** the LLM fails during consolidation, **When** handled, **Then** the endpoint returns an
   empty result rather than a 5xx error.

---

### User Story 6 - Embedding Backfill (Priority: P3)

An operator triggers a backfill to generate vector embeddings for any graph nodes that lack them.
This is an administrative maintenance operation used after bulk imports or schema migrations.

**Why this priority**: Operational hygiene; only required after data migrations or initial seeding.

**Independent Test**: Call `POST /v1/admin/backfill` and verify response contains `backfilled` integer.

**Acceptance Scenarios**:

1. **Given** nodes without embeddings exist, **When** `POST /v1/admin/backfill` is called with a
   `limit`, **Then** up to `limit` nodes are embedded and the count is returned.
2. **Given** a node already has an embedding, **When** backfill runs, **Then** that node is skipped.

---

### Edge Cases

- What happens when the LLM returns an internal generated ID as a triple term?
  → The system looks up the ID; if not found it drops the triple but keeps the log entry.
- What happens when a triple term is a placeholder word ("unknown", "none", "n/a")?
  → The term is rejected and the triple is dropped silently.
- How does the system handle a new predicate proposed by the LLM?
  → It is normalised (lowercase, snake_case), stored in the canonical map, and used for the edge.
- What happens when an existing relationship is superseded by a new fact?
  → The old relationship's weight is set to 0.0 (tombstoned); the new relationship is inserted.
- How does the system handle fact collision for entity nodes?
  → An LLM call decides "update" (overwrite content) vs "insert" (create a new node); "insert" is
  the safe fallback if the LLM call fails.
- What happens when graph expansion finds no neighbours at a hop?
  → Expansion terminates early; a partial graph up to that hop is returned.
- What if the final summary is under 100 characters?
  → The system performs one automatic retry before returning the short summary.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept free-text input and persist it as an episodic log entry with a
  unique identifier, embedding, user ID, domain, and optional source and timestamp.
- **FR-002**: The system MUST extract subject–predicate–object triples from ingested text using an LLM
  and persist each triple as typed entity nodes and typed relationship edges.
- **FR-003**: The system MUST resolve first-person pronouns (I, me, my) in extracted triples to the
  submitting user's canonical self-node.
- **FR-004**: The system MUST deduplicate entity nodes: a named entity of a given type MUST be
  stored once; subsequent ingestions MUST update the existing node and link it to the new log entry.
- **FR-005**: The system MUST normalise relationship predicates to lowercase snake_case.
- **FR-006**: The system MUST support LLM-proposed new predicates, adding approved predicates to
  the canonical predicate list for future extractions.
- **FR-007**: The system MUST evaluate whether a new relationship supersedes an existing one and, if
  so, tombstone the old relationship (set weight to 0.0).
- **FR-008**: The system MUST support semantic vector search across entity nodes and relationship edges,
  returning results ranked by a combined temporal decay and reinforcement relevance score.
- **FR-009**: Search results MUST be filterable by user ID, node type list, domain, and minimum weight.
- **FR-010**: The system MUST support multi-hop BFS graph expansion from seed node UUIDs, pruning each
  hop's frontier by a combined cosine similarity and observation-count score.
- **FR-011**: The system MUST guarantee a minimum number of SELF-node direct neighbours survive pruning
  during first-hop expansion when the SELF node is a seed.
- **FR-012**: The system MUST generate LLM-grounded summaries of a retrieved graph neighbourhood,
  supporting three query modes: broad profile, question-answering, and free-form.
- **FR-013**: Graph summarisation MUST perform a two-pass retrieval loop: pass 1 expands and drafts,
  pass 2 retrieves gap-filling context identified by the LLM, and both graphs are merged before final
  summarisation.
- **FR-014**: Graph summarisation MUST automatically retry the final LLM call if the produced summary
  is fewer than 100 characters.
- **FR-015**: The system MUST support memory consolidation: distilling recent episodic log entries into
  up to 3 durable factual statements re-ingested into the `core_memory` domain.
- **FR-016**: The system MUST support an administrative embedding backfill operation that generates
  vector embeddings for nodes missing them, bounded by a caller-supplied limit.
- **FR-017**: The system MUST expose a health-check endpoint returning `{status: ok}`.
- **FR-018**: The system MUST expose an endpoint returning the current canonical node types and
  allowed predicate vocabulary.
- **FR-019**: All knowledge operations (ingest, search, expand, summarise, consolidate) MUST be
  scoped to a `user_id`; data from different users MUST NOT be co-mingled.
- **FR-020**: All write-path operations MUST be idempotent by content hash where applicable (entity
  nodes keyed by type + normalised name).

### Key Entities

- **Log Node** (`node_type: "log"`): Raw episodic text entry. Has embedding, weight 0.3, short
  half-life (30 days). Source of truth for all ingested text.
- **Entity Node** (typed, e.g., `"person"`, `"place"`, `"project"`): A deduplicated real-world
  concept. Has embedding, weight ≥ 0.55, long half-life (365 days). Keyed by `(node_type, name)` hash.
- **Edge** (in `relationships` collection): A directed subject–predicate–object relationship between
  two entity nodes. Has embedding, weight 0.8, half-life 90 days. Can be tombstoned (weight → 0.0).
- **Canonical Map**: In-memory, database-backed vocabulary of allowed node types and predicate strings.
  Seeded at startup; extended at runtime by new-predicate ingestion.
- **Domain**: A namespace string (`"general"`, `"core_memory"`, etc.) for partitioning knowledge
  within a user's graph.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of submitted text results in a stored episodic log entry regardless of whether
  structured triples are extractable.
- **SC-002**: Named entities mentioned more than once across ingestions are deduplicated: the graph
  contains exactly one node per unique (type, name) pair per user.
- **SC-003**: Search results contain no data belonging to a user other than the requesting user.
- **SC-004**: Graph expansion terminates within a bounded number of nodes per hop (`limit_per_edge`)
  without returning tombstoned (weight 0.0) nodes or edges.
- **SC-005**: Summarisation produces a non-empty, structured markdown response for any non-empty
  graph neighbourhood.
- **SC-006**: Memory consolidation is idempotent: calling it multiple times does not continuously
  grow the `core_memory` domain with duplicate statements.
- **SC-007**: The health endpoint responds successfully as long as the FastAPI process is running,
  without requiring a live Firestore or LLM connection.

## Assumptions

- Clients authenticate externally; the API itself performs no authentication — `user_id` is caller-supplied.
- The LLM and embedding services (Vertex AI / Gemini) are available and pre-configured via environment
  variables; the API does not manage LLM credentials.
- Firestore is the only supported persistence backend; no alternative storage backends are in scope.
- The canonical predicate and node-type vocabulary is seeded from Firestore at startup; the application
  does not bootstrap from static files.
- Multi-user isolation is enforced only at the query/filter level via `user_id`; Firestore-level
  security rules are out of scope for this specification.
- The `SELF` token resolution produces a single stable self-node per user; the system does not support
  multiple personas or identity aliases per user.
