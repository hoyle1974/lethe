import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from lethe.graph.canonical_map import CanonicalMap
from lethe.models.node import GraphExpandResponse, Node


def _make_test_client(mock_embedder=None, mock_llm=None, mock_db=None):
    from lethe.config import Config
    from lethe.deps import get_canonical_map
    from lethe.main import app

    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-proj"}, clear=True):
        cfg = Config()

    app.state.config = cfg
    app.state.db = mock_db or MagicMock()
    app.state.embedder = mock_embedder
    app.state.llm = mock_llm
    app.dependency_overrides[get_canonical_map] = lambda: CanonicalMap()
    return TestClient(app, raise_server_exceptions=True)


def test_health():
    client = _make_test_client()
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ingest_status_none_returns_entry_uuid(mock_embedder, mock_llm):
    """When LLM returns status:none, ingest stores the log and returns entry_uuid."""
    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
    mock_doc_ref.update = AsyncMock()

    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.stream = AsyncMock(  # noqa: E501
        return_value=_async_iter([])
    )

    # LLM returns no triples
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post("/v1/ingest", json={"text": "Hello world"})
    assert resp.status_code == 201
    data = resp.json()
    assert "entry_uuid" in data
    assert isinstance(data["nodes_created"], list)
    assert isinstance(data["relationships_created"], list)


def _async_iter(items):
    """Return an async iterator over items."""

    async def _gen():
        for item in items:
            yield item

    return _gen()


def test_get_node_not_found(mock_embedder, mock_llm):
    mock_snap = AsyncMock()
    mock_snap.exists = False
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value.get = AsyncMock(return_value=mock_snap)

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.get("/v1/nodes/nonexistent-uuid")
    assert resp.status_code == 404


def test_get_node_types(mock_embedder, mock_llm):
    mock_doc_snap = MagicMock()
    mock_doc_snap.exists = False
    mock_doc_ref = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=mock_doc_snap)
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.get("/v1/node-types")
    assert resp.status_code == 200
    data = resp.json()
    assert "node_types" in data
    assert "allowed_predicates" in data
    assert "generic" in data["node_types"]


def test_delete_node_method_not_allowed(mock_embedder, mock_llm):
    client = _make_test_client(mock_embedder, mock_llm)
    resp = client.delete("/v1/nodes/some-uuid")
    assert resp.status_code == 405


def test_delete_entry_method_not_allowed(mock_embedder, mock_llm):
    client = _make_test_client(mock_embedder, mock_llm)
    resp = client.delete("/v1/entries/some-uuid")
    assert resp.status_code == 405


def test_graph_summarize_runs_iterative_reasoning_loop(mock_embedder, mock_llm):
    first_expand = GraphExpandResponse(
        nodes={
            "seed-1": Node(
                uuid="seed-1",
                node_type="person",
                content="Alice",
                journal_entry_ids=[],
            ),
            "target-1": Node(
                uuid="target-1",
                node_type="generic",
                content="Acme",
                journal_entry_ids=[],
            ),
        },
        edges=[],
    )
    second_expand = GraphExpandResponse(
        nodes={
            "seed-1": Node(
                uuid="seed-1",
                node_type="person",
                content="Alice",
                journal_entry_ids=[],
            ),
            "target-1": Node(
                uuid="target-1",
                node_type="generic",
                content="Acme",
                journal_entry_ids=[],
            ),
        },
        edges=[],
    )
    graph_expand_mock = AsyncMock(side_effect=[first_expand, second_expand])
    search_mock = AsyncMock(
        return_value=([Node(uuid="target-1", node_type="generic", content="Acme")], [])
    )
    llm_dispatch_mock = AsyncMock(
        side_effect=[
            "Draft summary paragraph",
            "Acme Corp",
            (
                "Alex Reed is a lead engineer at TechFlow, works closely with teammates, "
                "and is actively balancing multiple responsibilities across project planning, "
                "technical delivery, and ongoing collaboration while keeping personal obligations in view."  # noqa: E501
            ),
        ]
    )
    mock_llm.dispatch = llm_dispatch_mock
    client = _make_test_client(mock_embedder, mock_llm)

    with (
        patch("lethe.routers.graph.graph_expand", graph_expand_mock),
        patch("lethe.routers.graph.execute_search", search_mock),
    ):
        resp = client.post(
            "/v1/graph/summarize",
            json={"seed_ids": ["seed-1"], "query": "Who is Alice?", "debug": False},
        )

    assert resp.status_code == 200
    assert "lead engineer at TechFlow" in resp.json()["summary"]
    assert graph_expand_mock.await_count == 2
    second_call = graph_expand_mock.await_args_list[1].kwargs
    assert second_call["seed_ids"] == ["target-1"]
    assert second_call["hops"] == 1
    assert second_call["limit_per_edge"] == 20
    assert second_call["self_seed_neighbor_floor"] == 40
    assert search_mock.await_count == 1
    assert llm_dispatch_mock.await_count == 3


def test_graph_summarize_debug_mode_returns_reasoning(mock_embedder, mock_llm):
    first_expand = GraphExpandResponse(
        nodes={
            "seed-1": Node(
                uuid="seed-1",
                node_type="person",
                content="Alice",
                journal_entry_ids=[],
            ),
            "target-1": Node(
                uuid="target-1",
                node_type="generic",
                content="Acme",
                journal_entry_ids=[],
            ),
        },
        edges=[],
    )
    second_expand = GraphExpandResponse(
        nodes={
            "target-1": Node(
                uuid="target-1",
                node_type="generic",
                content="Acme",
                journal_entry_ids=["j1", "j2"],
            )
        },
        edges=[],
    )
    graph_expand_mock = AsyncMock(side_effect=[first_expand, second_expand])
    search_mock = AsyncMock(
        return_value=([Node(uuid="target-1", node_type="generic", content="Acme")], [])
    )
    llm_dispatch_mock = AsyncMock(
        side_effect=[
            "Draft summary paragraph",
            "Acme",
            (
                "Alex Reed is a lead engineer at TechFlow, works closely with teammates, "
                "and is actively balancing multiple responsibilities across project planning, "
                "technical delivery, and ongoing collaboration while keeping personal obligations in view."  # noqa: E501
            ),
        ]
    )
    mock_llm.dispatch = llm_dispatch_mock
    client = _make_test_client(mock_embedder, mock_llm)

    with (
        patch("lethe.routers.graph.graph_expand", graph_expand_mock),
        patch("lethe.routers.graph.execute_search", search_mock),
    ):
        resp = client.post(
            "/v1/graph/summarize",
            json={"seed_ids": ["seed-1"], "query": "Who is Alice?", "debug": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "lead engineer at TechFlow" in data["summary"]
    assert "debug_reasoning" in data
    assert data["debug_reasoning"]["target_queries"] == ["Acme"]
    assert data["debug_reasoning"]["retrieval_seed_ids"] == ["target-1"]
    assert data["debug_reasoning"]["pass2"]["performed"] is True


def test_graph_summarize_ignores_non_uuid_thought_tokens(mock_embedder, mock_llm):
    first_expand = GraphExpandResponse(
        nodes={
            "seed-1": Node(
                uuid="seed-1",
                node_type="person",
                content="Alex Reed",
                journal_entry_ids=[],
            )
        },
        edges=[],
    )
    graph_expand_mock = AsyncMock(side_effect=[first_expand])
    search_mock = AsyncMock(return_value=([], []))
    llm_dispatch_mock = AsyncMock(
        side_effect=[
            "Draft summary paragraph",
            "entity\nTechFlow\nAlex Reed\nworks",
            (
                "Alex Reed is a lead engineer at TechFlow, works closely with teammates, "
                "and is actively balancing multiple responsibilities across project planning, "
                "technical delivery, and ongoing collaboration while keeping personal obligations in view."  # noqa: E501
            ),
        ]
    )
    mock_llm.dispatch = llm_dispatch_mock
    client = _make_test_client(mock_embedder, mock_llm)

    with (
        patch("lethe.routers.graph.graph_expand", graph_expand_mock),
        patch("lethe.routers.graph.execute_search", search_mock),
    ):
        resp = client.post(
            "/v1/graph/summarize",
            json={"seed_ids": ["seed-1"], "query": "alex", "debug": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "lead engineer at TechFlow" in data["summary"]
    assert data["debug_reasoning"]["target_queries"] == ["entity", "TechFlow", "Alex Reed"]
    assert data["debug_reasoning"]["retrieval_seed_ids"] == []
    assert data["debug_reasoning"]["pass2"]["performed"] is False
    assert graph_expand_mock.await_count == 1


def test_graph_summarize_retries_when_final_summary_too_short(mock_embedder, mock_llm):
    first_expand = GraphExpandResponse(
        nodes={
            "seed-1": Node(
                uuid="seed-1",
                node_type="person",
                content="Alex Reed",
                journal_entry_ids=[],
            )
        },
        edges=[],
    )
    graph_expand_mock = AsyncMock(side_effect=[first_expand])
    search_mock = AsyncMock(return_value=([], []))
    llm_dispatch_mock = AsyncMock(
        side_effect=[
            "Draft summary paragraph",
            "NONE",
            "Too short.",
            "This is a sufficiently long final summary paragraph that includes detailed facts about Alex Reed and should be returned.",  # noqa: E501
        ]
    )
    mock_llm.dispatch = llm_dispatch_mock
    client = _make_test_client(mock_embedder, mock_llm)

    with (
        patch("lethe.routers.graph.graph_expand", graph_expand_mock),
        patch("lethe.routers.graph.execute_search", search_mock),
    ):
        resp = client.post(
            "/v1/graph/summarize",
            json={"seed_ids": ["seed-1"], "query": "alex", "debug": False},
        )

    assert resp.status_code == 200
    assert "sufficiently long final summary paragraph" in resp.json()["summary"]
    assert llm_dispatch_mock.await_count == 4


def test_graph_summarize_broad_query_disables_semantic_pruning(mock_embedder, mock_llm):
    first_expand = GraphExpandResponse(
        nodes={
            "seed-1": Node(
                uuid="seed-1",
                node_type="person",
                content="Alex Reed",
                journal_entry_ids=[],
            )
        },
        edges=[],
    )
    graph_expand_mock = AsyncMock(side_effect=[first_expand])
    search_mock = AsyncMock(return_value=([], []))
    llm_dispatch_mock = AsyncMock(
        side_effect=[
            "Draft summary paragraph",
            "NONE",
            (
                "Alex Reed is a lead engineer at TechFlow, works closely with teammates, "
                "and is actively balancing multiple responsibilities across project planning, "
                "technical delivery, and ongoing collaboration while keeping personal obligations in view."  # noqa: E501
            ),
        ]
    )
    mock_llm.dispatch = llm_dispatch_mock
    client = _make_test_client(mock_embedder, mock_llm)

    with (
        patch("lethe.routers.graph.graph_expand", graph_expand_mock),
        patch("lethe.routers.graph.execute_search", search_mock),
    ):
        resp = client.post(
            "/v1/graph/summarize",
            json={"seed_ids": ["seed-1"], "query": "alex", "debug": True},
        )

    assert resp.status_code == 200
    first_call = graph_expand_mock.await_args_list[0].kwargs
    assert first_call["query"] is None
    assert resp.json()["debug_reasoning"]["broad_query_mode"] is True


def test_graph_summarize_question_query_returns_answer_evidence_shape(mock_embedder, mock_llm):
    first_expand = GraphExpandResponse(
        nodes={
            "seed-1": Node(
                uuid="seed-1",
                node_type="person",
                content="Alex Reed",
                journal_entry_ids=[],
            ),
            "seed-2": Node(
                uuid="seed-2",
                node_type="entity",
                content="Buster",
                journal_entry_ids=[],
            ),
        },
        edges=[],
    )
    graph_expand_mock = AsyncMock(side_effect=[first_expand])
    search_mock = AsyncMock(return_value=([], []))
    llm_dispatch_mock = AsyncMock(
        side_effect=[
            "Answer: Alex's dog is Buster.\n\nEvidence:\n- Alex lives with Buster.",
            "NONE",
            "Answer: Alex's dog is Buster.\n\nEvidence:\n- Alex lives with Buster.\n- Buster has a vet appointment record.",  # noqa: E501
        ]
    )
    mock_llm.dispatch = llm_dispatch_mock
    client = _make_test_client(mock_embedder, mock_llm)

    with (
        patch("lethe.routers.graph.graph_expand", graph_expand_mock),
        patch("lethe.routers.graph.execute_search", search_mock),
    ):
        resp = client.post(
            "/v1/graph/summarize",
            json={"seed_ids": ["seed-1"], "query": "Who is Alex's dog?", "debug": True},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "Answer:" in body["summary"]
    assert "Evidence:" in body["summary"]
    assert body["debug_reasoning"]["question_query_mode"] is True


# --- Task 3: EMBEDDING_TASK_RETRIEVAL_DOCUMENT removed from admin.py backfill ---


def test_backfill_does_not_pass_explicit_embedding_task():
    """The backfill endpoint must call embedder.embed(content) without an explicit task arg."""
    import inspect

    import lethe.routers.admin as m

    src = inspect.getsource(m.backfill)
    assert "EMBEDDING_TASK_RETRIEVAL_DOCUMENT" not in src, (
        "backfill still passes EMBEDDING_TASK_RETRIEVAL_DOCUMENT explicitly; "
        "remove it — it's the default"
    )


# --- Task 4: db, embedder, llm type annotations in ingest.py ---


def test_ingest_router_has_typed_dependencies():
    """db, embedder, and llm in ingest must have explicit type annotations."""
    import inspect

    import lethe.routers.ingest as m

    src = inspect.getsource(m.ingest)
    assert "db: firestore.AsyncClient" in src, (
        "ingest is missing 'db: firestore.AsyncClient' annotation"
    )
    assert "embedder: Embedder" in src, "ingest is missing 'embedder: Embedder' annotation"
    assert "llm: LLMDispatcher" in src, "ingest is missing 'llm: LLMDispatcher' annotation"


# --- Security: GraphExpandRequest query length + prompt delimiter ---


def test_graph_expand_request_rejects_query_over_500_chars():
    from pydantic import ValidationError

    from lethe.models.node import GraphExpandRequest

    with pytest.raises(ValidationError):
        GraphExpandRequest(seed_ids=[], query="x" * 501)


def test_graph_expand_request_accepts_query_at_500_chars():
    from lethe.models.node import GraphExpandRequest

    req = GraphExpandRequest(seed_ids=[], query="x" * 500)
    assert len(req.query) == 500


def test_safe_query_wraps_in_delimiters():
    from lethe.routers.graph import _safe_query

    assert _safe_query("find Alice") == "<query>find Alice</query>"


def test_safe_query_empty_string():
    from lethe.routers.graph import _safe_query

    assert _safe_query("") == "<query></query>"


def test_summarize_system_prompts_use_safe_query():
    """Raw {q} must not appear in system-prompt f-strings; _safe_query must gate all uses."""
    import inspect

    import lethe.routers.graph as m

    src = inspect.getsource(m.summarize)
    assert "_safe_query" in src, "summarize must use _safe_query to wrap user query in prompts"
    assert 'f"...{q}' not in src, "raw {q} interpolation found in summarize prompt strings"


# --- Test setup: canonical map wiring ---


def test_make_test_client_overrides_canonical_map_dependency():
    """_make_test_client must use dependency_overrides, not app.state.canonical_map."""
    import inspect

    import tests.test_routers as tr

    src = inspect.getsource(tr._make_test_client)
    assert "dependency_overrides" in src, (
        "_make_test_client should use app.dependency_overrides to inject CanonicalMap; "
        "app.state.canonical_map is never read by get_canonical_map"
    )
    assert "app.state.canonical_map" not in src, (
        "app.state.canonical_map is dead code; get_canonical_map calls load_canonical_map(db) live"
    )


def test_get_canonical_map_reads_from_app_state_not_firestore():
    """get_canonical_map must return app.state.canonical_map directly (no Firestore call)."""
    import inspect

    from lethe.deps import get_canonical_map

    src = inspect.getsource(get_canonical_map)
    assert "load_canonical_map" not in src, (
        "get_canonical_map still calls load_canonical_map — it should read from app.state instead"
    )
    assert "app.state.canonical_map" in src, (
        "get_canonical_map must return request.app.state.canonical_map"
    )


# ---------------------------------------------------------------------------
# Backfill endpoint tests
# ---------------------------------------------------------------------------


def _make_doc_snap(doc_id: str, data: dict):
    """Create a mock Firestore document snapshot."""
    snap = MagicMock()
    snap.id = doc_id
    snap.to_dict.return_value = data
    return snap


def test_backfill_skips_docs_with_existing_embeddings(mock_embedder, mock_llm):
    """Docs that already have an embedding field must not be re-embedded."""
    doc_with_embedding = _make_doc_snap("doc-1", {"content": "hello", "embedding": [0.1] * 768})
    doc_without_embedding = _make_doc_snap("doc-2", {"content": "world"})

    mock_col = MagicMock()
    mock_col.limit.return_value.stream = MagicMock(
        return_value=_async_iter([doc_with_embedding, doc_without_embedding])
    )
    mock_doc_ref = AsyncMock()
    mock_doc_ref.update = AsyncMock()
    mock_col.document.return_value = mock_doc_ref

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_col

    embed_batch_mock = AsyncMock(return_value=[[0.5] * 768])
    mock_embedder.embed_batch = embed_batch_mock

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post("/v1/admin/backfill", json={"limit": 10})

    assert resp.status_code == 201
    data = resp.json()
    assert data["backfilled"] == 1
    embed_batch_mock.assert_awaited_once()
    call_args = embed_batch_mock.await_args
    assert call_args[0][0] == ["world"]


def test_backfill_embeds_docs_without_embeddings(mock_embedder, mock_llm):
    """Docs without embeddings should get embedded and have their Firestore doc updated."""
    doc_a = _make_doc_snap("doc-a", {"content": "foo"})
    doc_b = _make_doc_snap("doc-b", {"content": "bar"})

    mock_col = MagicMock()
    mock_col.limit.return_value.stream = MagicMock(return_value=_async_iter([doc_a, doc_b]))
    mock_doc_ref = AsyncMock()
    mock_doc_ref.update = AsyncMock()
    mock_col.document.return_value = mock_doc_ref

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_col

    vectors = [[0.1] * 768, [0.2] * 768]
    embed_batch_mock = AsyncMock(return_value=vectors)
    mock_embedder.embed_batch = embed_batch_mock

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post("/v1/admin/backfill", json={"limit": 10})

    assert resp.status_code == 201
    assert resp.json()["backfilled"] == 2
    assert mock_doc_ref.update.await_count == 2


def test_backfill_uses_embed_batch(mock_embedder, mock_llm):
    """Verify via source inspection that embed_batch is used, not sequential embed calls."""
    import inspect

    import lethe.routers.admin as admin_module

    source = inspect.getsource(admin_module.backfill)
    assert "embed_batch" in source, "backfill must use embed_batch"
    lines = [ln for ln in source.splitlines() if "embedder.embed(" in ln]
    assert lines == [], f"backfill must not call embedder.embed() directly; found: {lines}"


def _make_corpus_mock_db(mock_doc_ref=None):
    """Return a MagicMock db suitable for corpus ingest tests.

    Includes a properly mocked Firestore transaction so that
    create_relationship_node (corpus→document edge) doesn't crash.
    """
    if mock_doc_ref is None:
        mock_doc_ref = AsyncMock()
        mock_doc_ref.set = AsyncMock()
        mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
        mock_doc_ref.update = AsyncMock()

    mock_transaction = MagicMock()
    mock_transaction._begin = AsyncMock(return_value=None)
    mock_transaction._commit = AsyncMock(return_value=[])
    mock_transaction._rollback = AsyncMock()
    mock_transaction._clean_up = MagicMock()
    mock_transaction.set = MagicMock()
    mock_transaction.update = MagicMock()
    mock_transaction.in_progress = False

    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.stream = AsyncMock(  # noqa: E501
        return_value=_async_iter([])
    )
    mock_db.transaction.return_value = mock_transaction
    return mock_db


def test_corpus_ingest_generates_corpus_id(mock_embedder, mock_llm):
    """Corpus endpoint returns a corpus_id when none is provided."""
    mock_db = _make_corpus_mock_db()
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={"documents": [{"text": "Alice works at Acme.", "filename": "notes.txt"}]},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "corpus_id" in data
    assert isinstance(data["corpus_id"], str)
    assert len(data["corpus_id"]) > 0


def test_corpus_ingest_accepts_explicit_corpus_id(mock_embedder, mock_llm):
    """Provided corpus_id is preserved in the response."""
    mock_db = _make_corpus_mock_db()
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={
            "corpus_id": "my-corpus-abc",
            "documents": [{"text": "Bob runs engineering.", "filename": "notes.txt"}],
        },
    )
    assert resp.status_code == 202
    assert resp.json()["corpus_id"] == "my-corpus-abc"


def test_corpus_ingest_returns_document_ids(mock_embedder, mock_llm):
    """One document_id is returned per submitted document. chunk_ids/total_chunks are 0 in the
    immediate 202 response — they are populated by the background pipeline."""
    mock_db = _make_corpus_mock_db()
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={
            "documents": [
                {"text": "Doc one content.", "filename": "a.txt"},
                {"text": "Doc two content.", "filename": "b.txt"},
            ]
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert len(data["document_ids"]) == 2
    # chunk_ids and total_chunks are empty/0 in the immediate response (written async)
    assert data["total_chunks"] == 0
    assert data["chunk_ids"] == []


def test_corpus_ingest_rejects_empty_documents(mock_embedder, mock_llm):
    """Empty documents list is rejected with 422."""
    client = _make_test_client(mock_embedder, mock_llm)
    resp = client.post(
        "/v1/ingest/corpus",
        json={"documents": []},
    )
    assert resp.status_code == 422


def test_corpus_ingest_response_contains_chunk_ids_field(mock_embedder, mock_llm):
    """Response always includes a chunk_ids field (empty list in the immediate 202)."""
    mock_db = _make_corpus_mock_db()
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={"documents": [{"text": "Alice works at Acme.", "filename": "notes.txt"}]},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "chunk_ids" in data
    assert isinstance(data["chunk_ids"], list)


def test_corpus_ingest_chunk_nodes_use_chunk_type(mock_embedder, mock_llm):
    """Each chunk is stored as node_type='chunk'. Background task runs before TestClient returns."""
    written_node_types: list[str] = []

    async def capturing_set(data, **kwargs):
        if isinstance(data, dict) and "node_type" in data:
            written_node_types.append(data["node_type"])

    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = capturing_set
    mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
    mock_doc_ref.update = AsyncMock()
    mock_db = _make_corpus_mock_db(mock_doc_ref)
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={"documents": [{"text": "Alice works at Acme.", "filename": "notes.txt"}]},
    )
    assert resp.status_code == 202
    assert "chunk" in written_node_types


def test_corpus_ingest_llm_called_twice_per_doc_not_per_chunk(mock_embedder):
    """Hub-and-spoke: LLM called exactly twice per document (summary + extraction),
    regardless of how many chunks the document produces."""
    multi_para = "\n\n".join([f"paragraph {i} words." for i in range(5)])

    dispatch_calls: list = []

    class TrackingLLM:
        async def dispatch(self, req):
            dispatch_calls.append(req)
            return "status: none"

    mock_db = _make_corpus_mock_db()

    client = _make_test_client(mock_embedder, TrackingLLM(), mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={
            "documents": [{"text": multi_para, "filename": "notes.txt"}],
            "chunk_size": 2,
        },
    )
    assert resp.status_code == 202
    # Exactly 2 LLM calls per document: 1 for summarize_document + 1 for extract_triples(summary)
    assert len(dispatch_calls) == 2


def test_corpus_ingest_response_contains_corpus_node_id(mock_embedder, mock_llm):
    """Response includes a corpus_node_id and a corpus node is written with node_type='corpus'."""
    written_node_types: list[str] = []

    async def capturing_set(data, **kwargs):
        if isinstance(data, dict) and "node_type" in data:
            written_node_types.append(data["node_type"])

    mock_doc_ref = AsyncMock()
    mock_doc_ref.set = capturing_set
    mock_doc_ref.get = AsyncMock(return_value=MagicMock(exists=False))
    mock_doc_ref.update = AsyncMock()
    mock_db = _make_corpus_mock_db(mock_doc_ref)
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus",
        json={"documents": [{"text": "Alice works at Acme.", "filename": "notes.txt"}]},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "corpus_node_id" in data, "response must include corpus_node_id"
    assert isinstance(data["corpus_node_id"], str)
    assert len(data["corpus_node_id"]) > 0
    assert "corpus" in written_node_types, "a corpus node must be written to Firestore"


def test_corpus_document_endpoint_processes_single_doc(mock_embedder, mock_llm):
    """Fan-out endpoint /v1/ingest/corpus/document processes one document and returns 201."""
    mock_db = _make_corpus_mock_db()
    mock_llm._response = "status: none"

    client = _make_test_client(mock_embedder, mock_llm, mock_db)
    resp = client.post(
        "/v1/ingest/corpus/document",
        json={
            "corpus_id": "my-corpus",
            "corpus_node_id": "corpus_abc123",
            "doc_id": "doc_def456",
            "doc": {"text": "Alice works at Acme Corp.", "filename": "notes.txt"},
            "is_new": True,
            "ts": "2026-01-01T00:00:00+00:00",
            "doc_idx": 0,
            "total_docs": 1,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["doc_id"] == "doc_def456"
    assert "chunk_ids" in data
    assert "nodes_created" in data
