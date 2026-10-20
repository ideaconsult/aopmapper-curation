from scripts.verify_assay_results import cross_track_totals, ke_example, ke_support_counts


def test_ke_support_counts():
    cons = {"A": [{"support": 3}], "B": [{"support": 1}], "C": []}
    r = ke_support_counts(cons, 4)
    assert r == {"ke_at_threshold": 1, "ke_only_below": 1, "ke_without_record": 2}


def test_cross_track_totals():
    rows = [{"n_assay_triples_scored": "10", "n_matched": "5", "n_matched_strict": "2",
             "cross_track_consistency": "0.5", "cross_track_consistency_strict": "0.2",
             "n_ke_scored": "3"},
            {"n_assay_triples_scored": "10", "n_matched": "1", "n_matched_strict": "1",
             "cross_track_consistency": "0.1", "cross_track_consistency_strict": "0.1",
             "n_ke_scored": "4"}]
    r = cross_track_totals(rows)
    assert r["pooled"] == 0.3 and r["pooled_strict"] == 0.15 and r["max_n_ke_scored"] == 4


def test_ke_example():
    c = {"K": [{"support": 2, "assay": {"assay_name": "x"}}]}
    assert ke_example(c, "K")["n_clusters"] == 1 and ke_example(c, "Z")["n_clusters"] == 0
