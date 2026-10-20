"""Tests for publishing assay_technique_stats.json (tasks/manuscript_publish.py)."""
import json

from tasks.manuscript_publish import publish_technique_stats


def test_publishes_verbatim(tmp_path):
    src = tmp_path / "assay_technique_stats.json"
    src.write_text(json.dumps({"n_techniques": 3}), encoding="utf-8")
    dest = publish_technique_stats(str(src), tmp_path / "repo")
    assert dest == tmp_path / "repo" / "manuscript" / "generated" / "assay_technique_stats.json"
    assert dest.read_bytes() == src.read_bytes()


def test_noop_without_source(tmp_path):
    assert publish_technique_stats(None, tmp_path) is None
    assert publish_technique_stats(str(tmp_path / "missing.json"), tmp_path) is None
    assert not (tmp_path / "manuscript").exists()
