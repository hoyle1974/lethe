import pytest
from lethe.graph.ensure_node import (
    stable_entity_doc_id,
    stable_rel_id,
    normalized_predicate,
    _looks_like_entity_doc_id,
)


def test_stable_entity_doc_id_deterministic():
    id1 = stable_entity_doc_id("person", "Alice Smith")
    id2 = stable_entity_doc_id("person", "alice smith")
    assert id1 == id2
    assert id1.startswith("entity_")


def test_stable_entity_doc_id_different_types():
    person_id = stable_entity_doc_id("person", "Acme")
    project_id = stable_entity_doc_id("project", "Acme")
    assert person_id != project_id


def test_stable_rel_id_deterministic():
    id1 = stable_rel_id("subj1", "works_at", "obj1")
    id2 = stable_rel_id("subj1", "works_at", "obj1")
    assert id1 == id2
    assert id1.startswith("rel_")


def test_stable_rel_id_order_matters():
    id1 = stable_rel_id("subj1", "works_at", "obj1")
    id2 = stable_rel_id("obj1", "works_at", "subj1")
    assert id1 != id2


def test_normalized_predicate():
    assert normalized_predicate("Works At") == "works_at"
    assert normalized_predicate("WORKS_AT") == "works_at"
    assert normalized_predicate("  works at  ") == "works_at"
    assert normalized_predicate("NEW:mentors") == "mentors"
    assert normalized_predicate("new:mentors") == "mentors"


def test_looks_like_entity_doc_id():
    assert _looks_like_entity_doc_id("entity_3579d6dd3611a4b7e3cbdb79e5a29698b937bb4e")
    assert _looks_like_entity_doc_id("ENTITY_3579D6DD3611A4B7E3CBDB79E5A29698B937BB4E")
    assert not _looks_like_entity_doc_id("entity_abc123")
    assert not _looks_like_entity_doc_id("Alex Reed")
