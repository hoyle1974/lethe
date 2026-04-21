from lethe.graph.ensure_node import (
    _looks_like_entity_doc_id,
    normalized_predicate,
    stable_entity_doc_id,
    stable_rel_id,
    stable_self_id,
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


def test_stable_self_id_deterministic_per_user():
    id1 = stable_self_id("alex_reed_2026")
    id2 = stable_self_id("alex_reed_2026")
    assert id1 == id2
    assert id1.startswith("entity_")


def test_stable_self_id_differs_across_users():
    alex = stable_self_id("alex_reed_2026")
    jamie = stable_self_id("jamie_2026")
    assert alex != jamie


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


def test_doc_to_edge_populates_all_fields():
    from lethe.graph.ensure_node import doc_to_edge

    data = {
        "subject_uuid": "entity_aaa",
        "predicate": "works_at",
        "object_uuid": "entity_bbb",
        "content": "Alice works_at Acme",
        "weight": 0.8,
        "domain": "general",
        "user_id": "global",
        "source": None,
        "journal_entry_ids": ["log_1"],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
    }
    edge = doc_to_edge("rel_abc123", data)
    assert edge.uuid == "rel_abc123"
    assert edge.subject_uuid == "entity_aaa"
    assert edge.predicate == "works_at"
    assert edge.object_uuid == "entity_bbb"
    assert edge.content == "Alice works_at Acme"
    assert edge.weight == 0.8
    assert edge.journal_entry_ids == ["log_1"]
    assert edge.created_at is not None


def test_doc_to_edge_strips_vector_distance():
    from lethe.graph.ensure_node import doc_to_edge

    data = {
        "subject_uuid": "s",
        "predicate": "p",
        "object_uuid": "o",
        "vector_distance": 0.15,  # must be stripped
    }
    edge = doc_to_edge("rel_x", data)
    assert edge.uuid == "rel_x"


def test_add_entity_link_does_not_exist():
    """add_entity_link should be gone — traversal no longer uses entity_links."""
    import lethe.graph.ensure_node as m

    assert not hasattr(m, "add_entity_link"), (
        "add_entity_link still exists; remove it and its callers"
    )


def test_new_node_data_uses_default_domain():
    """node_data dicts in ensure_node must set domain=DEFAULT_DOMAIN, not NODE_TYPE_ENTITY."""
    import inspect

    import lethe.graph.ensure_node as m

    src = inspect.getsource(m.ensure_node)
    assert '"domain": NODE_TYPE_ENTITY' not in src, (
        "ensure_node sets domain=NODE_TYPE_ENTITY; use DEFAULT_DOMAIN instead"
    )


def test_collision_update_reuses_computed_vector():
    """collision update must use the already-computed vector, not call embedder.embed again."""
    import inspect

    import lethe.graph.ensure_node as m

    src = inspect.getsource(m.ensure_node)
    assert "new_vector = await embedder.embed" not in src, (
        "ensure_node calls embedder.embed twice on collision update; reuse `vector` instead"
    )


def test_create_relationship_node_targets_relationships_collection():
    """create_relationship_node must use lethe_relationships_collection, not lethe_collection."""
    import inspect

    import lethe.graph.ensure_node as m

    src = inspect.getsource(m.create_relationship_node)
    assert "lethe_relationships_collection" in src, (
        "create_relationship_node must reference config.lethe_relationships_collection"
    )
    # Confirm the old nodes-collection reference is gone from this function
    fn_body = src.split("def create_relationship_node")[1]
    assert "lethe_collection" not in fn_body
