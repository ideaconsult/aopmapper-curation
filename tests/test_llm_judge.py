"""Unit tests for tasks/llm_judge.py — the parts that don't need a network call."""
import pytest

from tasks.llm_judge import JudgeClient, build_triple_message, _fmt_slot


def term(name, iri=None):
    return {"name": name, "iri": iri}


def test_parse_json_plain():
    assert JudgeClient._parse_json('{"verdict": "correct", "score": 1.0}') \
        == {"verdict": "correct", "score": 1.0}


def test_parse_json_strips_code_fence():
    raw = '```json\n{"equivalent": true}\n```'
    assert JudgeClient._parse_json(raw) == {"equivalent": True}


def test_parse_json_strips_bare_fence():
    assert JudgeClient._parse_json('```\n{"a": 1}\n```') == {"a": 1}


def test_fmt_slot_absent():
    assert _fmt_slot(None) == "(absent)"


def test_fmt_slot_with_iri():
    assert _fmt_slot(term("DNA", "GO_x")) == "DNA [GO_x]"


def test_build_triple_message_contains_slots_and_truncates():
    long_desc = "x" * 5000
    msg = build_triple_message("Title", long_desc, "Tissue",
                               {"object": term("DNA"), "action": term("increased")})
    assert "biological object:  DNA" in msg
    assert "biological action:  increased" in msg
    assert "biological process: (absent)" in msg
    assert "..." in msg                 # description truncated at 2000 chars
    assert len(msg) < 2500
