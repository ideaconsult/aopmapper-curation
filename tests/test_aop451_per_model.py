"""Tests for the per-model AOP 451 block and main-text table fragment (tasks/aop451_per_model.py)."""
import pytest

from tasks.aop451_per_model import (display_name, hf_id, panel_pooled_row, per_model_block,
                                    per_model_rows, render_table, short_name, write_table)


def _res(matched, curated, recovered, total=19):
    return {"kec": {"n_matched_total": matched, "n_curated_total": curated,
                    "recall": matched / curated},
            "assays": {"n_recovered": recovered, "n_reference_assays": total,
                       "recovery_rate": recovered / total}}


RESULTS = {
    "org--model-a": _res(6, 21, 5),
    "org--model-b": _res(3, 21, 3),
    "nvidia--NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4": _res(3, 21, 6),
}


def test_names():
    assert short_name("nvidia--NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4") == \
        "NVIDIA-Nemotron-3-Super-120B"
    assert hf_id("org--model-a") == "org/model-a"


def test_rows_cover_all_models_in_order():
    rows = per_model_rows(RESULTS)
    assert [r["model"] for r in rows] == list(RESULTS)
    assert rows[0]["kec_n_matched"] == 6 and rows[0]["assay_n_reference"] == 19


def test_summary_extremes_and_median():
    s = per_model_block(RESULTS)["summary"]["kec_recall"]
    assert s["max"]["models"] == ["org--model-a"]
    assert s["min"]["value"] == 3 / 21
    assert len(s["min"]["models"]) == 2  # tie reported in full
    assert s["median"] == 3 / 21
    a = per_model_block(RESULTS)["summary"]["assay_recovery_rate"]
    assert a["max"]["short_names"] == ["NVIDIA-Nemotron-3-Super-120B"]
    assert a["min"]["models"] == ["org--model-b"]


def test_missing_model_results_are_skipped_in_summary():
    block = per_model_block({**RESULTS, "org--empty": {}})
    assert block["n_models"] == 4
    assert block["summary"]["kec_recall"]["n_models"] == 3


@pytest.mark.parametrize("key, expected", [
    ("BrainForge--Devstral-2-123B-Instruct-2512-NVFP4", "Devstral-2-123B"),
    ("nvidia--NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4", "Nemotron-3-Super-120B"),
    ("poolside--Laguna-S-2.1-NVFP4", "Laguna-S-2.1"),
    ("radm--Athene-V2-Chat-AWQ", "Athene-V2-Chat"),
    ("ibm-granite--granite-4.0-h-small-FP8", "granite-4.0-h-small"),
    ("google--gemma-4-26B-A4B-it", "gemma-4-26B-A4B-it"),
    ("google--gemma-3-27b-it", "gemma-3-27b-it"),
    ("Qwen--Qwen3.6-27B", "Qwen3.6-27B"),
    ("rdtand--Gemma4-31B-IT-PrismaQuant-5.5bit-vllm", "Gemma4-31B-IT (5.5 bit)"),
    ("rdtand--Gemma4-31B-IT-PrismaQuant-6bit-vllm", "Gemma4-31B-IT (6 bit)"),
])
def test_display_name(key, expected):
    assert display_name(key) == expected


def test_write_table_fragment(tmp_path):
    md = tmp_path / "generated" / "aop451_per_model_table.md"
    assert write_table(per_model_rows(RESULTS), md) == 3
    assert list(tmp_path.rglob("*.tsv")) == []  # no supplementary / TSV companion
    lines = md.read_text(encoding="utf-8").split("\n")
    assert lines[0] == ("| Model | KEC triples recovered | KEC recall (%) | "
                        "Recommended methods matched | Matched (%) |")
    assert lines[1] == "|---|---:|---:|---:|---:|"
    assert lines[2] == "| model-a | 6 | 28.6 | 5 | 26.3 |"
    assert lines[4] == "| Nemotron-3-Super-120B | 3 | 14.3 | 6 | 31.6 |"
    assert lines[5] == ""
    assert lines[6] == (
        "Table: AOP 451 reference check for each model of the three-model panel: recovery of "
        "the 21 curated KEC triples of the pathway in AOP-Wiki, and match to the 19 "
        "standardized assays recommended in Figure 1 of @nymark2021. {#tbl:aop451-per-model}")
    assert lines[7:] == [""]


PANEL_KEC = {"n_models": 3, "totals": {"n_curated": 21, "n_recovered_any": 14}}
CONSENSUS = {"n_models": 3, "n_reference_assays": 19, "n_matched_any": 5,
             "matched_any_rate": 5 / 19}


def test_panel_pooled_row_reads_existing_outputs():
    p = panel_pooled_row(PANEL_KEC, CONSENSUS)
    assert (p["kec_n_matched"], p["kec_n_curated"]) == (14, 21)
    assert p["kec_recall"] == 14 / 21
    assert (p["assay_n_recovered"], p["assay_n_reference"]) == (5, 19)
    assert p["assay_recovery_rate"] == 5 / 19
    assert p["pool_min_support"] == 1 and p["n_models"] == 3


@pytest.mark.parametrize("bad", [None, {}, {"n_reference_assays": 19}])
def test_panel_pooled_row_refuses_missing_assay_score(bad):
    with pytest.raises(ValueError, match="pooled-panel assay score"):
        panel_pooled_row(PANEL_KEC, bad)


def test_write_table_with_panel_row(tmp_path):
    md = tmp_path / "t.md"
    panel = panel_pooled_row(PANEL_KEC, CONSENSUS)
    assert write_table(per_model_rows(RESULTS), md, panel) == 3  # models, not rows
    lines = md.read_text(encoding="utf-8").split("\n")
    assert lines[4] == "| Nemotron-3-Super-120B | 3 | 14.3 | 6 | 31.6 |"
    assert lines[5] == "| **Panel (pooled)** | **14** | **66.7** | **5** | **26.3** |"
    assert lines[6] == ""
    assert lines[7] == (
        "Table: AOP 451 reference check for each model of the three-model panel: recovery of "
        "the 21 curated KEC triples of the pathway in AOP-Wiki, and match to the 19 "
        "standardized assays recommended in Figure 1 of @nymark2021. The last row pools the "
        "panel: a curated triple counts as recovered if at least one of the three models "
        "finds it, and a recommended method as matched if at least one of the three models "
        "matches it on its own. {#tbl:aop451-per-model}")


def test_table_without_panel_has_no_pooled_row():
    text = render_table(per_model_rows(RESULTS))
    assert "Panel (pooled)" not in text and "pools the panel" not in text


def test_table_has_one_row_per_model_and_ten_model_wording():
    results = {f"org--m{i}": _res(i, 21, i) for i in range(10)}
    text = render_table(per_model_rows(results))
    assert text.count("\n| ") == 10
    assert "ten-model panel" in text
