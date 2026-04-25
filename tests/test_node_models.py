from lethe.models.node import (
    Edge,
    GraphExpandRequest,
    GraphExpandResponse,
    GraphSummarizeResponse,
    IngestRequest,
    IngestResponse,
    Node,
    SearchRequest,
)


def test_node_defaults():
    n = Node(uuid="abc", node_type="generic", content="hello")
    assert n.user_id == "global"
    assert n.metadata == "{}"
    assert n.weight == 0.5
    assert n.domain == "general"


def test_ingest_request_defaults():
    req = IngestRequest(text="some text")
    assert req.user_id == "global"
    assert req.domain == "general"
    assert req.timestamp is None
    assert req.source is None


def test_ingest_response_fields():
    r = IngestResponse(entry_uuid="e1")
    assert r.nodes_created == []
    assert r.nodes_updated == []
    assert r.relationships_created == []


def test_search_request_defaults():
    req = SearchRequest(query="test query")
    assert req.user_id == "global"
    assert req.limit == 20
    assert req.min_significance == 0.0
    assert req.node_types == []
    assert req.domain is None


def test_graph_expand_request_defaults():
    req = GraphExpandRequest(seed_ids=["uuid1"])
    assert req.user_id == "global"
    assert req.hops == 2
    assert req.limit_per_edge == 20
    assert req.debug is False
    assert req.query is None


def test_graph_expand_response():
    r = GraphExpandResponse(
        nodes={"uuid1": Node(uuid="uuid1", node_type="person", content="Alice")},
        edges=[
            Edge(uuid="rel_001", subject_uuid="uuid1", predicate="works_at", object_uuid="uuid2")
        ],
    )
    assert len(r.nodes) == 1
    assert len(r.edges) == 1


def test_graph_summarize_response():
    r = GraphSummarizeResponse(summary="One paragraph.")
    assert r.summary == "One paragraph."
    assert r.debug_reasoning is None


def test_graph_expand_to_markdown():
    r = GraphExpandResponse(
        nodes={
            "s1": Node(uuid="s1", node_type="person", content="Alice"),
            "o1": Node(uuid="o1", node_type="generic", content="Acme Corp"),
        },
        edges=[Edge(uuid="rel_001", subject_uuid="s1", predicate="works_at", object_uuid="o1")],
    )
    md = r.to_markdown(seed_ids=["s1"])
    assert "Alice" in md
    assert "works_at" in md
    assert "[SEED]" in md


def test_graph_expand_to_markdown_includes_log_snippet_from_self_nodes():
    """Falls back to log nodes already in self.nodes when no source_logs provided."""
    r = GraphExpandResponse(
        nodes={
            "entity": Node(
                uuid="entity",
                node_type="person",
                content="Alice",
                metadata='{"role":"engineer"}',
                journal_entry_ids=["log-old", "log-new"],
            ),
            "log-old": Node(uuid="log-old", node_type="log", content="Old note"),
            "log-new": Node(
                uuid="log-new",
                node_type="log",
                content="Recent note " + ("x" * 300),
            ),
        },
        edges=[],
    )
    md = r.to_markdown(seed_ids=["entity"])
    assert 'metadata={"role":"engineer"}' in md
    assert "[source]" in md
    source_line = next(line for line in md.splitlines() if "[source]" in line)
    snippet = source_line.split('"', 1)[1].rsplit('"', 1)[0]
    assert len(snippet) <= 250


def test_graph_expand_to_markdown_uses_source_logs_when_provided():
    """source_logs parameter takes priority over log nodes in self.nodes."""
    entity = Node(
        uuid="ent-1",
        node_type="person",
        content="Jack",
        journal_entry_ids=["log-1"],
    )
    log_node = Node(
        uuid="log-1",
        node_type="log",
        content="Jack started working on Lethe last month",
    )
    r = GraphExpandResponse(
        nodes={"ent-1": entity},  # log-1 NOT in self.nodes
        edges=[],
    )
    source_logs = {"ent-1": [log_node]}
    md = r.to_markdown(seed_ids=["ent-1"], source_logs=source_logs)
    assert "Jack started working on Lethe last month" in md
    assert "[source]" in md


def test_graph_expand_to_markdown_no_source_logs_no_snippet():
    """When no source_logs and no log nodes in self.nodes, no [source] line."""
    r = GraphExpandResponse(
        nodes={"ent-1": Node(uuid="ent-1", node_type="person", content="Alice")},
        edges=[],
    )
    md = r.to_markdown(seed_ids=["ent-1"])
    assert "[source]" not in md


def test_node_has_no_spo_fields():
    """Confirm SPO fields are no longer on Node — they belong to Edge."""
    n = Node(uuid="x", node_type="entity", content="Alice")
    assert not hasattr(n, "predicate"), "predicate should not be on Node"
    assert not hasattr(n, "subject_uuid"), "subject_uuid should not be on Node"
    assert not hasattr(n, "object_uuid"), "object_uuid should not be on Node"
    assert not hasattr(n, "entity_links"), "entity_links should not be on Node"
    assert not hasattr(n, "relevance_score"), "relevance_score should not be on Node"
