"""Tests for assay-name consolidation (tasks/assay_names.py)."""
import json

import pytest

from tasks.assay_names import build_synonym_map, punct_normalize, resolve


def test_missing_synonym_file_is_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_synonym_map(str(tmp_path / "nope.json"))


def test_synonym_map_groups_variants(tmp_path):
    f = tmp_path / "syn.json"
    f.write_text(json.dumps({"Western blot": ["Western-blotting", "immunoblot"]}),
                 encoding="utf-8")
    m = build_synonym_map(str(f))
    keys = {resolve(n, m) for n in ("Western blot", "western  blotting", "Immunoblot")}
    assert keys == {"western blot"}
    assert resolve("ELISA", m) == "elisa"  # unmapped names keep their punct-normalised key


def test_punct_normalize():
    assert punct_normalize(" RT-PCR (qPCR)/x ") == "rt pcr qpcr x"


def test_shipped_synonym_file_exists_and_loads():
    m = build_synonym_map("resources/assay_synonyms.json")
    assert m
