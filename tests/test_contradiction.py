from __future__ import annotations

import inspect

import pytest

from lethe.graph.contradiction import evaluate_relationship_supersedes, tombstone_relationship
from tests.conftest import MockLLM

# ---------------------------------------------------------------------------
# Source-inspection test: new_rel_id must NOT appear in tombstone_relationship
# ---------------------------------------------------------------------------


def test_tombstone_relationship_has_no_new_rel_id_param():
    sig = inspect.signature(tombstone_relationship)
    assert "new_rel_id" not in sig.parameters, (
        "tombstone_relationship still has dead parameter 'new_rel_id'"
    )


# ---------------------------------------------------------------------------
# FakeLLM is NOT defined in this module (Task 2 guard)
# ---------------------------------------------------------------------------


def test_fake_llm_not_defined_in_module():
    import tests.test_contradiction as m

    assert not hasattr(m, "FakeLLM"), "FakeLLM should have been removed from test_contradiction"


# ---------------------------------------------------------------------------
# Behavioral tests for tombstone_relationship
# ---------------------------------------------------------------------------


class _FakeSnapshot:
    def __init__(self, exists: bool):
        self.exists = exists


class _FakeDocRef:
    def __init__(self, exists: bool):
        self._exists = exists
        self.updates: list[dict] = []

    async def get(self):
        return _FakeSnapshot(self._exists)

    async def update(self, data: dict):
        self.updates.append(data)


class _FakeCollection:
    def __init__(self, doc_ref: _FakeDocRef):
        self._doc_ref = doc_ref

    def document(self, doc_id: str) -> _FakeDocRef:
        return self._doc_ref


class _FakeDB:
    def __init__(self, doc_ref: _FakeDocRef):
        self._doc_ref = doc_ref

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._doc_ref)


@pytest.mark.asyncio
async def test_tombstone_relationship_sets_weight_zero_when_doc_exists():
    doc_ref = _FakeDocRef(exists=True)
    db = _FakeDB(doc_ref)
    await tombstone_relationship(db, "relationships", "old-id")
    assert doc_ref.updates == [{"weight": 0.0}]


@pytest.mark.asyncio
async def test_tombstone_relationship_no_error_when_doc_missing():
    doc_ref = _FakeDocRef(exists=False)
    db = _FakeDB(doc_ref)
    # Should return without raising or calling update
    await tombstone_relationship(db, "relationships", "missing-id")
    assert doc_ref.updates == []


# ---------------------------------------------------------------------------
# evaluate_relationship_supersedes tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_supersedes_finds_uuid_in_response():
    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    llm = MockLLM(response=f"Supersede {uid} because moved.")
    out = await evaluate_relationship_supersedes(
        llm, "Alex lives_in NY", [(uid, "Alex lives_in SF")]
    )
    assert out == uid


@pytest.mark.asyncio
async def test_evaluate_supersedes_returns_none_without_match():
    llm = MockLLM(response="none")
    out = await evaluate_relationship_supersedes(
        llm, "Alex likes pizza", [("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "Alex likes pasta")]
    )
    assert out is None
