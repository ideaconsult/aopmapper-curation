"""Tests for Supplementary Table S2 rendering (tasks/lineage_table.py)."""
from pathlib import Path

import yaml

from tasks.lineage_table import CAPTION, render_lineage_table, write_lineage_table

REPO = Path(__file__).resolve().parent.parent


def _lineage():
    # deliberately unsorted; shared/union chosen so jaccard rounds to 2 decimals
    return {
        "pairs": [
            {"a": "x", "b": "y", "relation": "two quantizations", "label": "X vs. Y",
             "shared": 22, "union": 237, "jaccard": 22 / 237},
            {"a": "p", "b": "q", "relation": "quantization", "label": "P vs. its FP8 quantization",
             "shared": 181, "union": 287, "jaccard": 181 / 287},
            {"a": "m", "b": "n", "relation": "quantization",
             "shared": 100, "union": 200, "jaccard": 0.5},
        ],
        "distribution": {"n_pairs": 2556, "median": 0.2201, "p90": 0.3399, "max": 0.6307},
    }


def test_rows_sorted_by_jaccard_with_two_decimals():
    lines = render_lineage_table(_lineage()).splitlines()
    assert lines[0] == "| Pair | Relation | Shared | Union | Jaccard |"
    assert lines[1] == "|---|---|---:|---:|---:|"
    assert lines[2] == "| P vs. its FP8 quantization | quantization | 181 | 287 | 0.63 |"
    assert lines[3].endswith("| 100 | 200 | 0.50 |")
    assert lines[4] == "| X vs. Y | two quantizations | 22 | 237 | 0.09 |"


def test_missing_label_falls_back_to_model_keys():
    lines = render_lineage_table(_lineage()).splitlines()
    assert lines[3].startswith("| m vs. n | quantization |")


def test_all_pairs_row_and_caption():
    text = render_lineage_table(_lineage())
    lines = text.splitlines()
    assert lines[5] == ("| *All panel pairs (2,556)* | | | | "
                        "*median 0.22, 90th pct. 0.34, max 0.63* |")
    assert lines[6] == ""
    assert lines[7] == CAPTION
    assert "{#tbl:" not in text
    assert text.endswith(CAPTION + "\n")


def test_write(tmp_path):
    dest = tmp_path / "supp" / "table_s2_lineage_jaccard.md"
    assert write_lineage_table(_lineage(), dest) == 3
    assert dest.read_text(encoding="utf-8") == render_lineage_table(_lineage())


def test_example_config_pairs_carry_label_and_relation():
    cfg = yaml.safe_load((REPO / "env.manuscript.yaml.example").read_text(encoding="utf-8"))
    for pair in cfg["kec116_lineage_pairs"]:
        assert pair["label"] and pair["relation"]
