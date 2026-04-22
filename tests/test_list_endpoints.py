"""
Tests for server-side filtering in list_entries and list_nodes endpoints.

These tests verify that:
1. node_type filters are applied server-side (via Firestore .where() calls)
2. The overfetch multiplier is removed (limit is not multiplied)
3. Client-side node_type/domain checks are removed from the loop body
"""

from __future__ import annotations

import inspect
import os
from unittest.mock import MagicMock, patch

import lethe.routers.entries as entries_module
import lethe.routers.nodes as nodes_module

# ---------------------------------------------------------------------------
# Source-inspection tests — verify structure of the fixed implementation
# ---------------------------------------------------------------------------


class TestListEntriesSourceInspection:
    """Verify list_entries uses server-side node_type filtering."""

    def _src(self) -> str:
        return inspect.getsource(entries_module.list_entries)

    def test_node_type_log_where_clause_present(self):
        """list_entries must add a where(node_type == NODE_TYPE_LOG) filter."""
        src = self._src()
        assert 'FieldFilter("node_type"' in src, (
            'list_entries must call FieldFilter("node_type", ...) for server-side filtering'
        )

    def test_no_client_side_node_type_check(self):
        """list_entries must NOT have a client-side node_type != NODE_TYPE_LOG check."""
        src = self._src()
        assert 'node_type" != NODE_TYPE_LOG' not in src and ("!= NODE_TYPE_LOG" not in src), (
            "list_entries still has a client-side node_type != NODE_TYPE_LOG check; remove it"
        )

    def test_no_overfetch_multiplier(self):
        """list_entries must use limit(limit) not limit(limit * 10)."""
        src = self._src()
        assert "limit * 10" not in src, (
            "list_entries still uses limit * 10 overfetch; change to limit(limit)"
        )

    def test_node_type_log_still_referenced(self):
        """NODE_TYPE_LOG should still be referenced (in the where clause)."""
        src = self._src()
        assert "NODE_TYPE_LOG" in src, "NODE_TYPE_LOG must remain in list_entries source"


class TestListNodesSourceInspection:
    """Verify list_nodes uses server-side node_type and domain filtering."""

    def _src(self) -> str:
        return inspect.getsource(nodes_module.list_nodes)

    def test_node_type_where_clause_present(self):
        """list_nodes must add a where(node_type == node_type) filter when node_type given."""
        src = self._src()
        assert 'FieldFilter("node_type"' in src, (
            'list_nodes must call FieldFilter("node_type", ...) for server-side filtering'
        )

    def test_domain_where_clause_present(self):
        """list_nodes must add a where(domain == domain) filter when domain given."""
        src = self._src()
        assert 'FieldFilter("domain"' in src, (
            'list_nodes must call FieldFilter("domain", ...) for server-side filtering'
        )

    def test_no_client_side_node_type_check(self):
        """list_nodes must NOT have a client-side node_type check inside the loop."""
        src = self._src()
        # The pattern we want to eliminate: if node_type and data.get("node_type") != node_type
        assert 'data.get("node_type") != node_type' not in src, (
            "list_nodes still has a client-side node_type check; "
            "remove it after adding server-side filter"
        )

    def test_no_client_side_domain_check(self):
        """list_nodes must NOT have a client-side domain check inside the loop."""
        src = self._src()
        assert 'data.get("domain") != domain' not in src, (
            "list_nodes still has a client-side domain check; "
            "remove it after adding server-side filter"
        )

    def test_no_overfetch_multiplier(self):
        """list_nodes must not multiply limit by 5."""
        src = self._src()
        assert "* 5" not in src, (
            "list_nodes still uses * 5 overfetch multiplier; change to limit(limit + offset)"
        )


# ---------------------------------------------------------------------------
# Behavioral mock tests — verify Firestore .where() is called correctly
# ---------------------------------------------------------------------------


def _make_mock_doc(doc_id: str, data: dict):
    """Create a mock Firestore document snapshot."""
    doc = MagicMock()
    doc.id = doc_id
    doc.to_dict.return_value = data
    return doc


def _async_iter(items):
    async def _gen():
        for item in items:
            yield item

    return _gen()


def _build_mock_db(docs: list):
    """
    Build a mock Firestore db whose .where() chain returns the given docs.

    The chain is: col.where(...).where(...).limit(...).stream()
    We use a MagicMock that returns itself on .where() / .limit() so any
    chain length works, and stream() returns the async iterator.
    """
    query_mock = MagicMock()
    query_mock.where.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.stream = MagicMock(return_value=_async_iter(docs))

    col_mock = MagicMock()
    col_mock.where.return_value = query_mock

    db_mock = MagicMock()
    db_mock.collection.return_value = col_mock
    return db_mock, col_mock, query_mock


def _get_where_field_paths(col_mock, query_mock) -> list[str]:
    """
    Extract the field_path values from all FieldFilter objects passed to .where() calls
    across both the collection and query mocks.
    """
    field_paths = []
    for mock in (col_mock, query_mock):
        for c in mock.where.call_args_list:
            # where() is called with filter=FieldFilter(...) as a keyword arg
            ff = c.kwargs.get("filter") or (c.args[0] if c.args else None)
            if ff is not None and hasattr(ff, "field_path"):
                field_paths.append(ff.field_path)
    return field_paths


class TestListEntriesBehavioral:
    """Behavioral tests: list_entries calls Firestore with node_type filter."""

    def _make_client(self, mock_db):
        from lethe.config import Config
        from lethe.deps import get_canonical_map
        from lethe.graph.canonical_map import CanonicalMap
        from lethe.main import app

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-proj"}, clear=True):
            cfg = Config()

        from fastapi.testclient import TestClient

        app.state.config = cfg
        app.state.db = mock_db
        app.state.embedder = None
        app.state.llm = None
        app.dependency_overrides[get_canonical_map] = lambda: CanonicalMap()
        return TestClient(app, raise_server_exceptions=True)

    def test_list_entries_filters_node_type_server_side(self):
        """
        list_entries must call .where(FieldFilter('node_type', '==', 'log'))
        on the Firestore query, not filter client-side.
        """
        log_doc = _make_mock_doc(
            "doc1",
            {
                "user_id": "global",
                "node_type": "log",
                "content": "Hello",
                "created_at": "2024-01-01T00:00:00Z",
                "journal_entry_ids": [],
            },
        )
        mock_db, col_mock, query_mock = _build_mock_db([log_doc])

        client = self._make_client(mock_db)
        resp = client.get("/v1/entries?limit=5")

        assert resp.status_code == 200

        # Verify a where call with node_type was made on the query chain
        field_paths = _get_where_field_paths(col_mock, query_mock)
        assert "node_type" in field_paths, (
            f"list_entries did not call .where() with 'node_type' filter; "
            f"actual field_paths filtered on: {field_paths}. "
            "Filtering must be done server-side."
        )

    def test_list_entries_uses_correct_limit(self):
        """list_entries must call .limit(limit) not .limit(limit * 10)."""
        mock_db, col_mock, query_mock = _build_mock_db([])

        client = self._make_client(mock_db)
        resp = client.get("/v1/entries?limit=5")

        assert resp.status_code == 200

        limit_calls = query_mock.limit.call_args_list
        assert limit_calls, "query_mock.limit() was never called"
        limit_val = limit_calls[-1][0][0]  # positional arg of last limit() call
        assert limit_val == 5, (
            f"list_entries called limit({limit_val}) but expected limit(5); "
            "remove the * 10 overfetch multiplier"
        )


class TestListNodesBehavioral:
    """Behavioral tests: list_nodes calls Firestore with node_type / domain filters."""

    def _make_client(self, mock_db):
        from lethe.config import Config
        from lethe.deps import get_canonical_map
        from lethe.graph.canonical_map import CanonicalMap
        from lethe.main import app

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-proj"}, clear=True):
            cfg = Config()

        from fastapi.testclient import TestClient

        app.state.config = cfg
        app.state.db = mock_db
        app.state.embedder = None
        app.state.llm = None
        app.dependency_overrides[get_canonical_map] = lambda: CanonicalMap()
        return TestClient(app, raise_server_exceptions=True)

    def test_list_nodes_filters_node_type_server_side(self):
        """
        When node_type is given, list_nodes must call .where(FieldFilter('node_type', '==', ...))
        instead of filtering client-side.
        """
        entity_doc = _make_mock_doc(
            "doc1",
            {
                "user_id": "global",
                "node_type": "entity",
                "content": "Alice",
                "created_at": "2024-01-01T00:00:00Z",
                "journal_entry_ids": [],
            },
        )
        mock_db, col_mock, query_mock = _build_mock_db([entity_doc])

        client = self._make_client(mock_db)
        resp = client.get("/v1/nodes?node_type=entity&limit=5")

        assert resp.status_code == 200

        field_paths = _get_where_field_paths(col_mock, query_mock)
        assert "node_type" in field_paths, (
            f"list_nodes did not call .where() with 'node_type' filter; "
            f"actual field_paths filtered on: {field_paths}. "
            "Filtering must be done server-side."
        )

    def test_list_nodes_filters_domain_server_side(self):
        """
        When domain is given, list_nodes must call .where(FieldFilter('domain', '==', ...))
        instead of filtering client-side.
        """
        doc = _make_mock_doc(
            "doc1",
            {
                "user_id": "global",
                "node_type": "generic",
                "domain": "work",
                "content": "Project X",
                "created_at": "2024-01-01T00:00:00Z",
                "journal_entry_ids": [],
            },
        )
        mock_db, col_mock, query_mock = _build_mock_db([doc])

        client = self._make_client(mock_db)
        resp = client.get("/v1/nodes?domain=work&limit=5")

        assert resp.status_code == 200

        field_paths = _get_where_field_paths(col_mock, query_mock)
        assert "domain" in field_paths, (
            f"list_nodes did not call .where() with 'domain' filter; "
            f"actual field_paths filtered on: {field_paths}. "
            "Filtering must be done server-side."
        )

    def test_list_nodes_uses_correct_limit(self):
        """list_nodes must call .limit(limit + offset) not .limit((limit + offset) * 5)."""
        mock_db, col_mock, query_mock = _build_mock_db([])

        client = self._make_client(mock_db)
        resp = client.get("/v1/nodes?limit=5&offset=2")

        assert resp.status_code == 200

        limit_calls = query_mock.limit.call_args_list
        assert limit_calls, "query_mock.limit() was never called"
        limit_val = limit_calls[-1][0][0]
        assert limit_val == 7, (  # 5 + 2
            f"list_nodes called limit({limit_val}) but expected limit(7) = limit+offset; "
            "remove the * 5 overfetch multiplier"
        )

    def test_list_nodes_no_filters_uses_just_limit_plus_offset(self):
        """When no node_type or domain, limit should still be limit + offset (not * 5)."""
        mock_db, col_mock, query_mock = _build_mock_db([])

        client = self._make_client(mock_db)
        resp = client.get("/v1/nodes?limit=10&offset=0")

        assert resp.status_code == 200

        limit_calls = query_mock.limit.call_args_list
        assert limit_calls, "query_mock.limit() was never called"
        limit_val = limit_calls[-1][0][0]
        assert limit_val == 10, (
            f"list_nodes called limit({limit_val}) but expected limit(10) = limit+offset; "
            "remove the * 5 overfetch multiplier"
        )
