import pytest
from lethe.graph.extraction import (
    parse_refinery_output,
    resolve_pronoun,
    RefineryTriple,
    build_refinery_prompt,
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


def test_resolve_pronoun_drops_first_person_without_owner():
    result = resolve_pronoun("I", owner_name="")
    assert result is None


def test_resolve_pronoun_replaces_with_owner():
    result = resolve_pronoun("I", owner_name="Alice")
    assert result == "Alice"


def test_resolve_pronoun_passthrough_for_names():
    result = resolve_pronoun("Bob", owner_name="")
    assert result == "Bob"


def test_build_refinery_prompt_contains_text():
    prompt = build_refinery_prompt(
        node_types=["person", "generic"],
        allowed_predicates=["works_at"],
        text="Alice joined Acme.",
    )
    assert "Alice joined Acme." in prompt
    assert "person" in prompt
    assert "works_at" in prompt
