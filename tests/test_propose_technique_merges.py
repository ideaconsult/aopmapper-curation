"""Tests for scripts/propose_technique_merges.py (pair/risky logic, fake embedder)."""
import csv
from collections import Counter
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

_spec = importlib.util.spec_from_file_location(
    "propose_technique_merges",
    Path(__file__).resolve().parents[1] / "scripts" / "propose_technique_merges.py")
ptm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ptm)


def fake_embed(vectors: dict):
    """embed(names) -> L2-normalised rows from a name -> vector table."""
    def embed(names):
        m = np.array([vectors[n] for n in names], dtype=np.float32)
        return m / np.linalg.norm(m, axis=1, keepdims=True)
    return embed


def doc_set():
    tech = lambda i, n, kes: {"id": i, "type_s": "assay_technique", "name_t": n,
                              "ke_ids_ss": kes}
    rec = lambda t, ke: {"id": f"R{t}{ke}", "type_s": "ke_assay", "technique_id_s": t,
                         "ke_id_s": ke}
    return [
        tech("T1", "Western blot", ["KE1", "KE2"]),
        tech("T2", "Western-blotting", ["KE3"]),
        tech("T3", "RT-PCR", ["KE4"]),
        tech("T4", "qPCR", ["KE5"]),
        tech("T5", "Mass spectrometry", ["KE6"]),
        rec("T1", "KE1"), rec("T1", "KE1"), rec("T1", "KE2"), rec("T2", "KE3"),
        rec("T3", "KE4"), rec("T4", "KE5"), rec("T5", "KE6"),
    ]


VECS = {   # two near-parallel pairs, one unrelated technique
    "western blot": [1, 0.0, 0], "western blotting": [1, 0.1, 0],
    "rt pcr": [0, 1, 0.0], "qpcr": [0, 1, 0.1],
    "mass spectrometry": [0, 0, 1],
}


@pytest.mark.parametrize("a,b,expected", [
    ("western blot", "western blotting", False),     # inflection only
    ("rt pcr", "qpcr", True),
    ("elisa", "elispot", True),
    ("hplc", "uplc", True),
    ("oecd tg 455", "oecd tg 487", True),             # digits
    ("flow cytometry", "flow cytometric analysis", False),   # long non-digit tokens
    ("pcr", "rt pcr", True),                          # short one-sided token
    ("immunofluorescence and microscopy", "immunofluorescence microscopy", False),
    ("t3 level measurement", "t3 measurement", False),   # generic word only
    ("reporter assay", "reporter", False),
    ("comet assay", "comet test", False),            # neutral tokens ignored both sides
])
def test_is_risky(a, b, expected):
    assert ptm.is_risky(a, b) is expected


def test_jaccard():
    assert ptm.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_load_techniques_counts():
    techs = {t["id"]: t for t in ptm.load_techniques(doc_set())}
    assert techs["T1"]["n_ke"] == 2 and techs["T1"]["n_rec"] == 3
    assert techs["T2"]["norm"] == "western blotting"
    assert techs["T2"]["n_ke"] == 1 and techs["T2"]["n_rec"] == 1


def test_find_pairs_chunked_equals_dense():
    rng = np.random.default_rng(0)
    v = rng.normal(size=(40, 5)).astype(np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    assert ptm.find_pairs(v, 0.3, chunk=7) == ptm.find_pairs(v, 0.3, chunk=1000)


def test_propose_pairs_and_risky_flags():
    techs = ptm.load_techniques(doc_set())
    rows = ptm.propose(techs, fake_embed(VECS), min_sim=0.9)
    got = {(r["name_a"], r["name_b"]): r for r in rows}
    assert set(got) == {("Western blot", "Western-blotting"), ("RT-PCR", "qPCR")}
    wb = got[("Western blot", "Western-blotting")]
    assert wb["risky"] == 0 and wb["n_ke_a"] == 2 and wb["n_ke_b"] == 1
    assert wb["n_rec_a"] == 3 and wb["n_rec_b"] == 1
    assert got[("RT-PCR", "qPCR")]["risky"] == 1
    assert [r["sim"] for r in rows] == sorted((r["sim"] for r in rows), reverse=True)


def test_propose_is_deterministic():
    techs = ptm.load_techniques(doc_set())
    a = ptm.propose(techs, fake_embed(VECS), 0.9)
    b = ptm.propose(list(reversed(techs)), fake_embed(VECS), 0.9)
    assert a == b


def test_shared_normalised_name_is_sim_one_pair():
    docs = [{"id": "A", "type_s": "assay_technique", "name_t": "Foo Assay", "ke_ids_ss": ["K1"]},
            {"id": "B", "type_s": "assay_technique", "name_t": "foo-assay", "ke_ids_ss": ["K2"]}]
    # punct_normalize("foo-assay") = "foo assay" = punct_normalize("Foo Assay")
    rows = ptm.propose(ptm.load_techniques(docs), fake_embed({"foo assay": [1, 0]}), 0.8)
    assert len(rows) == 1 and rows[0]["sim"] == 1.0


def test_summary_bands_and_single_ke_neighbours():
    techs = ptm.load_techniques(doc_set())
    rows = ptm.propose(techs, fake_embed(VECS), 0.9)
    s = {x["band_start"]: x for x in ptm.summarise(techs, rows, 0.9)}
    assert set(s) == {0.9, 0.95}
    assert sum(x["pairs"] for x in s.values()) == len(rows) == 2
    # single-KE techniques: T2..T5; T2 (non-risky pair), T3, T4 (risky pair) have neighbours
    for b in (0.9, 0.95):
        assert s[b]["single_ke_with_neighbour"] == 3
        assert s[b]["single_ke_with_nonrisky_neighbour"] == 1
    assert s[0.9]["pairs"] == 0                       # both fake pairs have cos ~0.995
    assert s[0.95]["pairs_risky"] == 1 and s[0.95]["pairs_nonrisky"] == 1


def test_main_writes_tsv(tmp_path, monkeypatch):
    inp = tmp_path / "aop_solr_assay.json"
    inp.write_text(json.dumps(doc_set()), encoding="utf-8")
    monkeypatch.setattr(ptm, "default_embed", lambda model: fake_embed(VECS))
    assert ptm.main([str(inp), "--min-sim", "0.9"]) == 0
    with (tmp_path / "merge_candidates.tsv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    assert list(rows[0]) == ptm.COLUMNS and len(rows) == 2




# ---------------------------------------------------------------------------
# --apply (star grouping, threshold alone decides)
# ---------------------------------------------------------------------------

def row(a, b, sim, risky=0, ke_a=1, ke_b=1, rec_a=1, rec_b=1):
    return {"name_a": a, "name_b": b, "sim": sim, "risky": risky, "n_ke_a": ke_a,
            "n_ke_b": ke_b, "n_rec_a": rec_a, "n_rec_b": rec_b}


def test_plan_threshold_only_and_risky_applied_and_recorded():
    rows = [row("Kinase assay", "Kinase assays", 0.99),
            row("Foo assay", "Foo assays", 0.85),                 # below threshold
            row("RT-PCR", "qPCR", 0.99, risky=1)]                 # risky: still merged
    plan = ptm.plan_merges(rows, {}, 0.90)
    assert len(plan["groups"]) == 2
    flags = {p["variant"]: p["risky"] for p in plan["pairs"]}
    assert flags == {"Kinase assays": 0, "qPCR": 1} or flags == {"Kinase assays": 0,
                                                                   "RT-PCR": 1}
    assert "risky merges (sim >= threshold, applied anyway): 1" in \
        ptm.format_groups(plan["groups"], plan["pairs"])


def test_plan_canonical_is_most_ke_then_rec_then_alpha():
    plan = ptm.plan_merges([row("B assay x", "A assay x", 0.95, ke_a=3, ke_b=2)], {}, 0.9)
    assert plan["synonyms"] == {"B assay x": ["A assay x"]}
    plan = ptm.plan_merges([row("B x", "A x", 0.95, rec_a=1, rec_b=4)], {}, 0.9)
    assert plan["synonyms"] == {"A x": ["B x"]}
    plan = ptm.plan_merges([row("B x", "A x", 0.95)], {}, 0.9)
    assert plan["synonyms"] == {"A x": ["B x"]}


def test_plan_chain_does_not_drift():
    # A~B 0.95, B~C 0.95, A~C 0.85 (below threshold): only B joins A, C stays out
    rows = [row("Aaa", "Bbb", 0.95, ke_a=9), row("Bbb", "Ccc", 0.95),
            row("Aaa", "Ccc", 0.85)]
    plan = ptm.plan_merges(rows, {}, 0.9)
    assert plan["synonyms"] == {"Aaa": ["Bbb"]}
    assert [p["variant"] for p in plan["pairs"]] == ["Bbb"]


def test_plan_group_only_direct_neighbours_of_canonical():
    # A~B, A~C direct -> {A: B, C}; D only reachable through B -> not merged
    rows = [row("Aaa", "Bbb", 0.97, ke_a=9), row("Aaa", "Ccc", 0.93, ke_a=9),
            row("Bbb", "Ddd", 0.99)]
    plan = ptm.plan_merges(rows, {}, 0.9)
    assert plan["synonyms"] == {"Aaa": ["Bbb", "Ccc"]}


def test_plan_name_assigned_once_to_first_canonical():
    # C is a neighbour of both A (processed first, more KEs) and B
    rows = [row("Aaa", "Ccc", 0.92, ke_a=9), row("Bbb", "Ccc", 0.99, ke_a=5)]
    plan = ptm.plan_merges(rows, {}, 0.9)
    assert plan["synonyms"] == {"Aaa": ["Ccc"]}


def test_plan_existing_canonical_protected_and_extended():
    syn = {"western blot": ["immunoblot"]}
    plan = ptm.plan_merges(
        [row("Western blot", "Western blotting", 0.99, ke_a=1, ke_b=30)], syn, 0.9)
    # fewer KEs, but the existing canonical stays canonical; "western blotting" joins it
    assert plan["synonyms"] == {"western blot": ["immunoblot", "Western blotting"]} or \
        plan["synonyms"] == {"western blot": ["Western blotting", "immunoblot"]}
    assert plan["groups"][0]["status"] == "extended existing group"


def test_plan_existing_canonicals_never_merged_together_or_moved():
    syn = {"x assay": ["x"], "y assay": [], "z assay": ["zz"]}
    rows = [row("x assay", "y assay", 0.99, ke_a=5),       # two canonicals: untouched
            row("Zz", "New thing", 0.97)]                   # zz is a variant: keeps canon.
    plan = ptm.plan_merges(rows, syn, 0.9)
    assert plan["synonyms"] == syn and plan["pairs"] == []


def test_plan_does_not_mutate_input():
    syn = {"a": ["b"]}
    ptm.plan_merges([row("a", "c", 0.99)], syn, 0.9)
    assert syn == {"a": ["b"]}


def test_group_size_distribution_reported():
    rows = [row("Aaa", "Bbb", 0.95, ke_a=9), row("Aaa", "Ccc", 0.95, ke_a=9),
            row("Ddd", "Eee", 0.95)]
    plan = ptm.plan_merges(rows, {}, 0.9)
    assert "group size distribution (size:groups): 2:1, 3:1" in \
        ptm.format_groups(plan["groups"], plan["pairs"])


def test_apply_writes_idempotent_and_logs(tmp_path):
    syn, log, cand = (tmp_path / "syn.json", tmp_path / "applied.tsv",
                      tmp_path / "cand.tsv")
    syn.write_text(json.dumps({"ames test": ["ames assay"]}), encoding="utf-8")
    rows = [row("Kinase assay", "Kinase assays", 0.9912),
            row("Ames test", "Ames testing", 0.95),
            row("RT-PCR", "qPCR", 0.93, risky=1)]
    with cand.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ptm.COLUMNS, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    args = ["--apply", "--candidates", str(cand), "--synonyms", str(syn),
            "--applied-log", str(log)]
    assert ptm.main(args + ["--dry-run"]) == 0
    assert json.loads(syn.read_text(encoding="utf-8")) == {"ames test": ["ames assay"]}
    assert not log.exists()
    assert ptm.main(args) == 0
    first = syn.read_bytes(), log.read_bytes()
    after = json.loads(syn.read_text(encoding="utf-8"))
    assert after["ames test"] == ["ames assay", "Ames testing"]
    assert after["Kinase assay"] == ["Kinase assays"]
    assert len(after) == 3
    with log.open(encoding="utf-8") as f:
        logged = list(csv.DictReader(f, delimiter="\t"))
    assert list(logged[0]) == ptm.APPLIED_COLUMNS == ["variant", "canonical", "sim", "risky"]
    by_var = {r["variant"]: r for r in logged}
    assert set(by_var) == {"Kinase assays", "Ames testing", "qPCR"}
    assert sorted(r["risky"] for r in logged) == ["0", "0", "1"]
    assert ptm.main(args) == 0                       # second run: no change at all
    assert (syn.read_bytes(), log.read_bytes()) == first


# ---------------------------------------------------------------------------
# Hard veto (real false merges found in the first apply)
# ---------------------------------------------------------------------------

def norm(s):
    return ptm.punct_normalize(s)


@pytest.mark.parametrize("a,b,cat", [
    # roman numerals / digits
    ("Enzyme Activity Determination (Complex I)",
     "Enzyme Activity Determination (Complex II)", "digit_numeral"),
    ("Enzyme Activity Determination (Complex I)",
     "Enzyme Activity Determination (Complex III)", "digit_numeral"),
    ("Complex IV Activity Assay", "Complex V activity assay", "digit_numeral"),
    ("OECD TG 455", "OECD TG 487", "digit_numeral"),
    ("IL-6 ELISA", "IL-8 ELISA", "digit_numeral"),
    ("CYP1A induction", "CYP1B induction", "digit_numeral"),
    # greek / subunit
    ("TNF-alpha ELISA", "TNF-beta ELISA", "greek_subunit"),
    ("TNF-α ELISA", "TNF-β ELISA", "greek_subunit"),
    ("PPARgamma reporter assay", "PPAR reporter assay", "greek_subunit"),
    ("GABA A1 receptor binding", "GABA B2 receptor binding", "greek_subunit"),
    # antonyms / negation
    ("Direct quantification of L-Glu transporter activity",
     "Indirect quantification of L-Glu transporter activity", "antonym"),
    ("in vitro micronucleus assay", "in vivo micronucleus assay", "antonym"),
    ("positive control assay", "negative control assay", "antonym"),
    ("increase in ROS", "decrease in ROS", "antonym"),
    ("gene up regulation", "gene down regulation", "antonym"),
    ("p53 expression", "anti p53 expression", "antonym"),
    ("specific binding", "non-specific binding", "antonym"),
    ("active caspase", "inactive caspase", "antonym"),
])
def test_veto_real_false_merges(a, b, cat):
    assert ptm.veto_reason(norm(a), norm(b)) == cat
    assert ptm.veto_reason(norm(b), norm(a)) == cat       # symmetric


@pytest.mark.parametrize("a,b", [
    ("Kinase assay", "Kinase assays"),
    ("Micronucleus assay", "micronuclei assay"),
    ("31P NMR spectroscopy", "^31P NMR spectroscopy"),     # same digits, caret only
    ("TNF-α ELISA", "TNF-alpha ELISA"),                    # symbol vs spelled-out
    ("Quantitative PCR (qPCR)", "Quantitative polymerase chain reaction (QPCR)"),
    ("Electroencephalogram (EEG)", "Electroencephalography (EEG)"),
    ("NVS_IC_rNaCh_site 2", "NVS_IC_rNaCh_site2"),
    ("flow cytometry in vivo", "flow cytometry in vivo assay"),
])
def test_veto_does_not_fire_on_true_variants(a, b):
    assert ptm.veto_reason(norm(a), norm(b)) is None


def test_is_risky_roman_numerals_not_inflections():
    assert ptm.is_risky("complex i activity", "complex ii activity") is True
    assert ptm.is_risky("complex iv activity", "complex v activity") is True


def test_plan_veto_beats_threshold_and_risky_and_is_counted():
    rows = [row("Complex I assay", "Complex II assay", 0.999, ke_a=9),
            row("Direct binding", "Indirect binding", 0.999),
            row("Kinase assay", "Kinase assays", 0.95)]
    plan = ptm.plan_merges(rows, {}, 0.90)
    assert plan["synonyms"] == {"Kinase assay": ["Kinase assays"]}
    assert Counter(v["category"] for v in plan["vetoed"]) == {
        "digit_numeral": 1, "antonym": 1}
    text = ptm.format_groups(plan["groups"], plan["pairs"], vetoed=plan["vetoed"])
    assert "hard-vetoed pairs (sim >= threshold, never merged): 2" in text
    assert "digit_numeral=1" in text and "antonym=1" in text


def test_plan_veto_does_not_count_below_threshold():
    plan = ptm.plan_merges([row("Complex I", "Complex II", 0.5)], {}, 0.9)
    assert plan["vetoed"] == []


def test_audit_applied_flags_digit_and_word_differences(tmp_path):
    p = tmp_path / "applied.tsv"
    p.write_text("variant\tcanonical\tsim\trisky\n"
                 "Kinase assays\tKinase assay\t0.99\t0\n"
                 "Complex II assay\tComplex I assay\t0.95\t0\n"
                 "Positive control\tcontrol\t0.93\t0\n", encoding="utf-8")
    flagged = {x["variant"]: x["why"] for x in ptm.audit_applied(p)}
    assert set(flagged) == {"Complex II assay", "Positive control"}
    assert "veto:digit_numeral" in flagged["Complex II assay"]
    assert "word:positive" in flagged["Positive control"]
