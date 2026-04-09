from lethe.models.node import (
    Node, IngestRequest, IngestResponse,
    SearchRequest, GraphExpandRequest, GraphExpandResponse, GraphSummarizeResponse, Edge,
)


def test_node_defaults():
    n = Node(uuid="abc", node_type="generic", content="hello")
    assert n.user_id == "global"
    assert n.metadata == "{}"
    assert n.entity_links == []
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
    assert req.limit_per_edge == 5
    assert req.query is None


def test_graph_expand_response():
    r = GraphExpandResponse(
        nodes={"uuid1": Node(uuid="uuid1", node_type="person", content="Alice")},
        edges=[Edge(subject="uuid1", predicate="works_at", object="uuid2")],
    )
    assert len(r.nodes) == 1
    assert len(r.edges) == 1


def test_graph_summarize_response():
    r = GraphSummarizeResponse(summary="One paragraph.")
    assert r.summary == "One paragraph."


def test_graph_expand_to_markdown():
    r = GraphExpandResponse(
        nodes={
            "s1": Node(uuid="s1", node_type="person", content="Alice"),
            "o1": Node(uuid="o1", node_type="generic", content="Acme Corp"),
        },
        edges=[Edge(subject="s1", predicate="works_at", object="o1")],
    )
    md = r.to_markdown(seed_ids=["s1"])
    assert "Alice" in md
    assert "works_at" in md
    assert "[SEED]" in md
