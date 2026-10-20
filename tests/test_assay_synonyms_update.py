"""Tests for the synonym-map update logic (tasks/assay_synonym_merge.py), the library behind
tasks/assay_synonyms_update.py. Fake embedder: no model download."""
import csv
import json
import logging

import numpy as np

from tasks import assay_synonym_merge as m


def cluster(name, support=2):
    return {"support": support, "assay": {"assay_name": name}}


VIZ = {"kes": [
    {"ke_id": "KE1", "clusters": [cluster("Kinase assay"), cluster("Kinase assays"),
                                  cluster("Complex I assay"), cluster("Weak one", 1)]},
    {"ke_id": "KE2", "clusters": [cluster("Kinase assay"), cluster("Complex II assay")]},
    {"ke_id": "KE3", "clusters": [cluster("Kinase-assay")]},          # retired KE
]}

VECS = {   # kinase assay ~ kinase assays (cos ~0.995); complex i ~ complex ii (cos ~0.995)
    "kinase assay": [1, 0.0, 0], "kinase assays": [1, 0.1, 0],
    "complex i assay": [0, 1, 0.0], "complex ii assay": [0, 1, 0.1],
    "weak one": [0, 0, 1],
}


def fake_embed(names):
    mat = np.array([VECS[n] for n in names], dtype=np.float32)
    return mat / np.linalg.norm(mat, axis=1, keepdims=True)


def test_techniques_from_viz_counts_without_synonym_map():
    techs = {t["norm"]: t for t in m.techniques_from_viz(VIZ, {"KE1", "KE2"}, 2)}
    # "Kinase assay" and "Kinase assays" stay separate names (no map is applied)
    assert set(techs) == {"kinase assay", "kinase assays", "complex i assay",
                          "complex ii assay"}
    assert techs["kinase assay"]["n_ke"] == 2 and techs["kinase assay"]["n_rec"] == 2
    assert techs["kinase assays"]["n_ke"] == 1
    assert "weak one" not in techs                           # below min support


def test_techniques_from_viz_release_gate_and_no_gate():
    with_ke3 = {t["norm"]: t for t in m.techniques_from_viz(VIZ, None, 2)}
    assert with_ke3["kinase assay"]["n_ke"] == 3             # KE3 "Kinase-assay" normalises in
    gated = {t["norm"]: t for t in m.techniques_from_viz(VIZ, {"KE1", "KE2"}, 2)}
    assert gated["kinase assay"]["n_ke"] == 2


def setup_files(tmp_path, synonyms=None):
    syn = tmp_path / "syn.json"
    syn.write_text(json.dumps(synonyms or {}), encoding="utf-8")
    return syn, tmp_path / "applied.tsv"


def test_run_update_merges_vetoes_logs_and_is_idempotent(tmp_path, caplog):
    syn, applied = setup_files(tmp_path, {"ames test": ["ames assay"]})
    techs = m.techniques_from_viz(VIZ, {"KE1", "KE2"}, 2)
    with caplog.at_level(logging.WARNING, logger=m.logger.name):
        stats = m.run_update(techs, fake_embed, syn, applied, threshold=0.90)
    assert stats["n_new_merges"] == 1 and stats["n_new_groups"] == 1
    assert stats["n_vetoed"] == 1
    assert stats["vetoed_by_category"] == {"greek_subunit": 0, "digit_numeral": 1,
                                           "antonym": 0}
    assert stats["n_synonym_groups_before"] == 1 and stats["n_synonym_groups_after"] == 2
    after = json.loads(syn.read_text(encoding="utf-8"))
    assert after == {"ames test": ["ames assay"], "Kinase assay": ["Kinase assays"]}
    assert any(r.levelno == logging.WARNING and "1 new merges" in r.getMessage()
               for r in caplog.records)
    with applied.open(encoding="utf-8") as f:
        logged = list(csv.DictReader(f, delimiter="\t"))
    assert [(r["variant"], r["canonical"]) for r in logged] == [
        ("Kinase assays", "Kinase assay")]

    first = syn.read_bytes(), applied.read_bytes()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=m.logger.name):
        stats2 = m.run_update(techs, fake_embed, syn, applied, threshold=0.90)
    assert stats2["n_new_merges"] == 0 and stats2["n_applied_rows_appended"] == 0
    assert (syn.read_bytes(), applied.read_bytes()) == first     # nothing changes
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_run_update_veto_toggle(tmp_path):
    syn, applied = setup_files(tmp_path)
    techs = m.techniques_from_viz(VIZ, {"KE1", "KE2"}, 2)
    stats = m.run_update(techs, fake_embed, syn, applied, threshold=0.90, veto=False)
    assert stats["n_new_merges"] == 2 and stats["n_vetoed"] == 0
    assert stats["n_risky_new_merges"] >= 1                  # roman numeral pair is risky


def test_run_update_existing_canonical_untouched_and_threshold(tmp_path):
    syn, applied = setup_files(tmp_path, {"kinase assay": ["kinase assays"]})
    techs = m.techniques_from_viz(VIZ, {"KE1", "KE2"}, 2)
    before = syn.read_bytes()
    stats = m.run_update(techs, fake_embed, syn, applied, threshold=0.999)
    assert stats["n_new_merges"] == 0 and syn.read_bytes() == before
    assert not applied.exists()                              # nothing to append
