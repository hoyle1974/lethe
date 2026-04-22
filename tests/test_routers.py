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
    assert resp.status_code == 200
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
