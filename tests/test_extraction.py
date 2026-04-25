import lethe.graph.extraction as _extraction_module
from lethe.graph.extraction import (
    build_refinery_prompt,
    parse_refinery_output,
)


def test_parse_refinery_output_status_none():
    raw = "status: none"
    status, triples = parse_refinery_output(raw)
    assert status == "none"
    assert triples == []


def test_parse_refinery_output_ok_with_triples():
    raw = (
        "status: ok\n"
        "triples:\n"
        "Alice | works_at | Acme Corp | person | generic\n"
        "Bob | knows | Alice | person | person\n"
    )
    status, triples = parse_refinery_output(raw)
    assert status == "ok"
    assert len(triples) == 2
    assert triples[0].subject == "Alice"
    assert triples[0].predicate == "works_at"
    assert triples[0].object == "Acme Corp"
    assert triples[0].subject_type == "person"
    assert triples[0].object_type == "generic"


def test_parse_refinery_output_malformed_triple_skipped():
    raw = "status: ok\ntriples:\nBadLine\n"
    status, triples = parse_refinery_output(raw)
    assert status == "ok"
    assert triples == []


def test_parse_new_predicate_prefix():
    raw = "status: ok\ntriples:\nAlice | NEW:mentors | Bob | person | person\n"
    status, triples = parse_refinery_output(raw)
    assert len(triples) == 1
    assert triples[0].is_new_predicate is True
    assert triples[0].canonical_predicate == "mentors"


def test_parse_refinery_output_keeps_generated_id_terms_for_downstream_resolution():
    raw = (
        "status: ok\n"
        "triples:\n"
        "entity_3579d6dd3611a4b7e3cbdb79e5a29698b937bb4e | discusses | Aegis deadline | generic | event\n"  # noqa: E501
        "Jamie | related_to | entity_3c87170e333ac158c828015896e33f5b881312d3 | person | generic\n"
    )
    status, triples = parse_refinery_output(raw)
    assert status == "ok"
    assert len(triples) == 2


def test_build_refinery_prompt_contains_text():
    prompt = build_refinery_prompt(
        node_types=["person", "generic"],
        allowed_predicates=["works_at"],
        text="Alice joined Acme.",
    )
    assert "Alice joined Acme." in prompt
    assert "person" in prompt
    assert "works_at" in prompt


def test_resolve_pronoun_not_in_extraction_module():
    assert not hasattr(_extraction_module, "resolve_pronoun"), (
        "resolve_pronoun should have been removed from lethe.graph.extraction"
    )


def test_build_refinery_prompt_interpolates_owner_name():
    prompt = build_refinery_prompt(
        node_types=["person", "generic"],
        allowed_predicates=["works_at"],
        text="I joined Acme.",
        owner_name="Alice",
    )
    assert "Alice" in prompt, "owner_name 'Alice' should appear in the rendered prompt"


def test_build_refinery_prompt_omits_owner_rule_when_empty():
    prompt = build_refinery_prompt(
        node_types=["person", "generic"],
        allowed_predicates=["works_at"],
        text="I joined Acme.",
        owner_name="",
    )
    assert "owner_name" not in prompt.lower()


async def test_summarize_document_returns_llm_text():
    from unittest.mock import AsyncMock

    from lethe.graph.extraction import summarize_document

    mock_llm = AsyncMock()
    mock_llm.dispatch.return_value = "Alice works at Acme Corp and manages Bob."

    result = await summarize_document(
        mock_llm, text="Alice works at Acme. She manages Bob.", filename="notes.txt"
    )

    assert result == "Alice works at Acme Corp and manages Bob."
    assert mock_llm.dispatch.call_count == 1


async def test_summarize_document_truncates_at_char_limit():
    from lethe.constants import DOCUMENT_SUMMARY_CHAR_LIMIT
    from lethe.graph.extraction import summarize_document

    captured: list = []

    class CapturingLLM:
        async def dispatch(self, req):
            captured.append(req)
            return "summary"

    long_text = "x" * (DOCUMENT_SUMMARY_CHAR_LIMIT + 10_000)
    await summarize_document(CapturingLLM(), text=long_text, filename="big.txt")

    assert len(captured) == 1
    # Template preamble adds ~500 chars; allow 1 500 chars total overhead
    assert len(captured[0].user_prompt) < DOCUMENT_SUMMARY_CHAR_LIMIT + 1_500
