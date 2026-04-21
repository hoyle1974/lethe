from __future__ import annotations

from lethe.graph.ids import GENERATED_ID_RE, is_generated_id


def test_matches_entity_sha1():
    assert is_generated_id("entity_3579d6dd3611a4b7e3cbdb79e5a29698b937bb4e")


def test_matches_rel_sha1():
    assert is_generated_id("rel_3579d6dd3611a4b7e3cbdb79e5a29698b937bb4e")


def test_matches_uuid():
    assert is_generated_id("9f2e6f90-4be1-4e4a-8a69-0f1fdd853b7e")


def test_rejects_human_text():
    assert not is_generated_id("Project Aegis")


def test_rejects_entity_prefix_without_sha1():
    assert not is_generated_id("entity_short")


def test_regex_exported():
    assert GENERATED_ID_RE.pattern is not None
