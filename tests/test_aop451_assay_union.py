"""Tests for the pooled-panel assay union and the @tbl:aop451-assays-ref fragment
(tasks/aop451_per_model.py) and the cluster-vs-own divergence helper
(tasks/aop451_assay_diagnose.py)."""
import pytest

from tasks.aop451_assay_diagnose import divergent_methods
from tasks.aop451_per_model import (assay_reference_rows, assay_union, panel_pooled_row,
                                    render_assays_ref_table, write_assays_ref_table)

METHODS = [("Method A", None, ["MIE"]), ("Method B", None, ["KE1C"]),
           ("Method C", None, ["KE1A"]), ("Method D", "OECD TG476", ["KE3"]),
           ("Method E", None, ["KE1B", "KE2"])]


def _assays(recovered_idx, methods=METHODS):
    rows = [{"reference_assay": n, "guideline": g, "relevant_ke_groups": k,
             "recovered": i in recovered_idx, "best_match": None}
            for i, (n, g, k) in enumerate(methods)]
    return {"assays": rows, "n_reference_assays": len(rows), "n_recovered": len(recovered_idx),
            "recovery_rate": len(recovered_idx) / len(rows)}


def _res(recovered_idx, methods=METHODS):
    return {"kec": {}, "assays": _assays(recovered_idx, methods)}


# model-best matches 3 methods on its own; the others add one each (overlapping)
RESULTS = {"org--best": _res({0, 1, 4}), "org--b": _res({0, 2}), "org--c": _res({1}),
           "org--none": _res(set())}


def test_union_counts_and_per_method_models():
    u = assay_union(RESULTS)
    assert (u["n_models"], u["n_reference_assays"]) == (4, 5)
    assert u["n_matched_any"] == 4  # A, B, C, E; D is matched by nobody
    assert [m["n_models_matching"] for m in u["methods"]] == [2, 2, 1, 0, 1]
    assert u["methods"][0]["models_matching"] == ["org--best", "org--b"]
    assert u["matched_any_rate"] == 4 / 5
    assert u["per_model_n_recovered"]["org--best"] == 3
    assert u["best_single_n"] == 3 and u["best_single_models"] == ["org--best"]


def test_union_is_at_least_best_single_member():
    """A cluster-merged panel can score below its best member (the representative of the
    cluster a model's assay was merged into may no longer match the method); the union
    cannot. Here the cluster-scored panel finds 2 methods, the best model alone finds 3."""
    cluster_scored = {"assays": _assays({0, 1})["assays"], "n_recovered": 2}
    u = assay_union(RESULTS)
    assert cluster_scored["n_recovered"] < u["best_single_n"] <= u["n_matched_any"]
    # the divergence helper names exactly the method the cluster scoring misses
    assert divergent_methods(RESULTS, cluster_scored, "org--best") == [4]
    assert divergent_methods(RESULTS, cluster_scored, "org--b") == [2]
    assert divergent_methods(RESULTS, cluster_scored, "org--none") == []


def test_union_row_order_mismatch_is_rejected():
    swapped = [METHODS[1], METHODS[0]] + METHODS[2:]
    with pytest.raises(AssertionError, match="order differs"):
        assay_union({"org--a": _res({0}), "org--b": _res({0}, swapped)})


def test_union_rejects_model_without_assay_rows():
    with pytest.raises(ValueError, match="no per-method assay results"):
        assay_union({**RESULTS, "org--empty": {"kec": {}, "assays": {}}})
    assert assay_reference_rows(RESULTS)[1][3]["guideline"] == "OECD TG476"


def test_union_checks_reported_counts():
    bad = {"org--a": _res({0, 1})}
    bad["org--a"]["assays"]["n_recovered"] = 5
    with pytest.raises(AssertionError, match="reported n_recovered"):
        assay_union(bad)


def test_pooled_row_uses_union():
    pk = {"n_models": 4, "totals": {"n_curated": 21, "n_recovered_any": 14}}
    row = panel_pooled_row(pk, assay_union(RESULTS))
    assert (row["assay_n_recovered"], row["assay_n_reference"]) == (4, 5)
    assert row["assay_recovery_rate"] == 4 / 5
    assert row["assay_n_recovered"] >= 3  # never below the best single model


def test_assays_ref_table_fragment(tmp_path):
    md = tmp_path / "generated" / "aop451_assays_ref_table.md"
    assert write_assays_ref_table(assay_union(RESULTS), md) == 5
    lines = md.read_text(encoding="utf-8").split("\n")
    assert lines[0] == ("| Recommended method (Figure 1B) | KE group | Models matching (of 4) |")
    assert lines[1] == "|---|---|---:|"
    assert lines[2] == "| Method A | MIE | 2 |"
    assert lines[5] == "| Method D | KE3 | - |"  # zero shown as '-'
    assert lines[6] == "| Method E | KE1B, KE2 | 1 |"
    assert lines[7] == "| **Methods matched by at least one model** | | **4 / 5** |"
    assert lines[8] == ""
    assert lines[9].startswith(
        "Table: Standardized methods recommended in Figure 1B of @nymark2021, and how many "
        "of the four models propose a matching assay for each. The KE group column follows "
        "the Key-Event grouping of Figure 1 of @nymark2021 (")
    assert lines[9].endswith(" {#tbl:aop451-assays-ref}")
    assert "ecovery" not in "\n".join(lines)
    assert lines[10:] == [""]


def test_ten_model_caption_wording():
    results = {f"org--m{i}": _res({i % 5}) for i in range(10)}
    text = render_assays_ref_table(assay_union(results))
    assert "how many of the ten models propose a matching assay" in text
    assert "Models matching (of 10)" in text
