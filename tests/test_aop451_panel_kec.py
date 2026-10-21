"""Tests for the pooled-panel KEC recovery block and table fragment (tasks/aop451_per_model.py)."""
import copy

import pytest

from tasks.aop451_per_model import (check_match_semantics, panel_kec_recovery,
                                    render_panel_table, short_ke_title, write_panel_table)


def _v(matched, ref_idx, score=0.9):
    return {"matched": matched, "score": score, "method": "name" if matched else "none",
            "ref_idx": ref_idx}


def _ke(title, n_curated, verdicts):
    return {"title": title, "n_curated": n_curated, "n_llm_triples": len(verdicts),
            "n_matched": len({v["ref_idx"] for v in verdicts if v["matched"]}),
            "match_verdicts": verdicts}


def _model(ke1, ke2, ke3):
    return {"kec": {"per_ke": {"KE870": ke1, "KE1538": ke2, "KE1670": ke3}}}


TITLE1, TITLE2, TITLE3 = ("Increase, Cell Proliferation",
                          "Decreased protection against oxidative stress", "Lung cancer")

# Real structure of aop451_reference_check.json; tiny. KE870: 3 curated, KE1538: 0 curated,
# KE1670: 2 curated. Unmatched verdicts carry the nearest ref_idx and must not count.
RESULTS = {
    "org--model-a": _model(
        _ke(TITLE1, 3, [_v(True, 0), _v(True, 1), _v(False, 2, 0.5)]),
        _ke(TITLE2, 0, []),
        _ke(TITLE3, 2, [_v(True, 0)])),
    "org--model-b": _model(
        _ke(TITLE1, 3, [_v(True, 0)]),
        _ke(TITLE2, 0, []),
        _ke(TITLE3, 2, [_v(False, 0, 0.6), _v(True, 1)])),
    "org--model-c": _model(
        _ke(TITLE1, 3, [_v(True, 1), _v(False, 1, 0.7)]),
        _ke(TITLE2, 0, []),
        _ke(TITLE3, 2, [_v(False, 1, 0.3)])),
}


def test_semantics_hold_on_fixture():
    c = check_match_semantics(RESULTS)
    assert c["n_model_ke_checked"] == 9 and c["n_violations"] == 0


def test_semantics_violation_is_detected_and_blocks_the_block():
    bad = copy.deepcopy(RESULTS)
    bad["org--model-a"]["kec"]["per_ke"]["KE870"]["n_matched"] = 3  # distinct matched = 2
    c = check_match_semantics(bad)
    assert [x["kind"] for x in c["violations"]] == ["n_matched_mismatch"]
    with pytest.raises(ValueError, match="semantics violated"):
        panel_kec_recovery(bad, 2)


def test_ref_idx_out_of_range_is_a_violation():
    bad = copy.deepcopy(RESULTS)
    ke = bad["org--model-a"]["kec"]["per_ke"]["KE1670"]
    ke["match_verdicts"] = [_v(True, 5)]
    assert check_match_semantics(bad)["violations"][0]["kind"] == "ref_idx_out_of_range"


def test_per_ke_counts_tie_and_zero_curated():
    b = panel_kec_recovery(RESULTS, 2)
    assert [r["ke_id"] for r in b["per_ke"]] == ["KE870", "KE1538", "KE1670"]
    k1, k2, k3 = b["per_ke"]
    assert k1["triple_support"] == [2, 2, 0]
    assert (k1["n_curated"], k1["n_recovered_any"], k1["n_recovered_ge_min_support"]) == (3, 2, 2)
    assert (k1["best_single_n"], k1["best_single_models"], k1["n_models_any"]) == (
        2, ["org--model-a"], 3)
    # zero-curated KE: all zeros, every model tied at 0
    assert (k2["n_curated"], k2["n_recovered_any"], k2["best_single_n"], k2["n_models_any"]) == (
        0, 0, 0, 0)
    assert k2["triple_support"] == []
    assert k2["best_single_models"] == list(RESULTS)
    # tie at 1 between a and b; unmatched verdict of b/c ignored
    assert k3["triple_support"] == [1, 1]
    assert (k3["n_recovered_any"], k3["n_recovered_ge_min_support"]) == (2, 0)
    assert k3["best_single_models"] == ["org--model-a", "org--model-b"]
    assert k3["n_models_any"] == 2


def test_totals_and_distribution():
    b = panel_kec_recovery(RESULTS, 2)
    t = b["totals"]
    assert (t["n_curated"], t["n_recovered_any"], t["n_recovered_ge_min_support"]) == (5, 4, 2)
    # best model on the whole pathway (a: 2 + 0 + 1)
    assert t["sum_of_per_ke_best_single"] == 3
    assert (t["best_single_n"], t["best_single_models"]) == (3, ["org--model-a"])
    assert b["n_curated_triples_unrecovered"] == 1
    assert b["triple_support"] == [2, 2, 0, 1, 1]
    assert b["support_distribution"] == {"0": 1, "1": 2, "2": 2, "3": 0}
    assert b["n_models"] == 3 and b["min_support"] == 2


def test_min_support_comes_from_argument():
    assert panel_kec_recovery(RESULTS, 1)["totals"]["n_recovered_ge_min_support"] == 4
    assert panel_kec_recovery(RESULTS, 3)["totals"]["n_recovered_ge_min_support"] == 0


def test_models_without_kec_are_ignored():
    b = panel_kec_recovery({**RESULTS, "org--empty": {}}, 2)
    assert b["n_models"] == 3


@pytest.mark.parametrize("ke, title, expected", [
    ("KE870", "Increase, Cell Proliferation", "Increase, cell proliferation"),
    ("KE1115", "Increase, Reactive oxygen species", "Increase, reactive oxygen species"),
    ("KE1634", "Increase, Oxidative DNA damage", "Increase, oxidative DNA damage"),
    ("KE1496", "Increased, secretion of proinflammatory mediators",
     "Increased secretion of proinflammatory mediators"),
    ("KE2006", "Secondary genotoxicity", "Secondary genotoxicity"),
    ("KE1495", "Substance interaction with the lung resident cell membrane components",
     "Membrane interaction with lung resident cells (MIE)"),
    ("KE1670", "Lung cancer", "Lung cancer (AO)"),
])
def test_short_ke_title(ke, title, expected):
    assert short_ke_title(ke, title) == expected


def test_render_and_write_table(tmp_path):
    b = panel_kec_recovery(RESULTS, 2)
    md = tmp_path / "generated" / "aop451_kec_panel_table.md"
    assert write_panel_table(b, md) == 3
    lines = md.read_text(encoding="utf-8").split("\n")
    assert lines[0] == ("| Key Event | Curated triples | Recovered by at least 1 model | "
                        "Recovered by at least 2 models | Best single model |")
    assert lines[1] == "|---|---:|---:|---:|---:|"
    assert lines[2] == "| Increase, cell proliferation | 3 | 2 | 2 | 2 |"
    assert lines[3] == "| Decreased protection against oxidative stress | 0 | 0 | 0 | 0 |"
    assert lines[4] == "| Lung cancer (AO) | 2 | 2 | 0 | 1 |"
    assert lines[5] == "| **Total** | **5** | **4** | **2** | **3** |"
    assert lines[6] == ""
    assert lines[7] == (
        "Table: Recovery of the 5 curated KEC triples of AOP 451 by the three-model panel "
        "pooled: curated triples found by at least one model, and by at least two models, "
        "against the best individual model. Key Events with no curated triple are listed with "
        "zeros. {#tbl:aop451-kec}")
    assert lines[8:] == [""]


def test_ten_model_wording():
    results = {f"org--m{i}": copy.deepcopy(RESULTS["org--model-a"]) for i in range(10)}
    assert "ten-model panel" in render_panel_table(panel_kec_recovery(results, 2))
