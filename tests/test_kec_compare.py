"""Unit tests for the hybrid KEC triple matcher (tasks/kec_compare.py).

The embedding and judge tiers are exercised with stubs, so the tests are fast and
deterministic and need neither torch nor a network.
"""
import pytest

from tasks.kec_compare import (
    normalise_triple, _norm_name, match_slot, triple_match, match_ke,
    cluster_ke_triples, llm_triples,
)


# ── stubs ─────────────────────────────────────────────────────────────────────

class StubEmbedder:
    """cosine() returns a value looked up from a dict keyed by the unordered pair,
    defaulting to 0.0 (dissimilar)."""
    def __init__(self, sims: dict):
        self._sims = sims

    def cosine(self, a: str, b: str) -> float:
        return self._sims.get(frozenset((a, b)), 0.0)


def term(name, iri=None, source=None):
    return {"name": name, "iri": iri, "source": source}


# ── normalisation ─────────────────────────────────────────────────────────────

def test_normalise_structured_keys():
    t = {"object": term("DNA"), "action": term("increased")}   # no process
    n = normalise_triple(t)
    assert n["object"]["name"] == "DNA"
    assert n["action"]["name"] == "increased"
    assert n["process"] is None


def test_normalise_llm_keys():
    t = {"biological_object": term("DNA"),
         "biological_process": term("apoptosis"),
         "biological_action": term("increased")}
    n = normalise_triple(t)
    assert n["object"]["name"] == "DNA"
    assert n["process"]["name"] == "apoptosis"
    assert n["action"]["name"] == "increased"


def test_normalise_drops_empty_name():
    n = normalise_triple({"biological_object": {"name": "", "iri": None}})
    assert n["object"] is None


@pytest.mark.parametrize("a,b", [
    ("17beta-estradiol", "17beta estradiol"),
    ("Oxidative Stress", "oxidative_stress"),
    ("apoptosis,", "apoptosis"),
])
def test_norm_name_variants_collapse(a, b):
    assert _norm_name(a) == _norm_name(b)


# ── slot matching ─────────────────────────────────────────────────────────────

def test_action_exact_only():
    ok, sim, method = match_slot(term("decreased"), term("decreased"),
                                 is_action=True, embedder=None, tau=0.75)
    assert ok and method == "action_name"


def test_action_mismatch():
    ok, _, _ = match_slot(term("decreased"), term("increased"),
                          is_action=True, embedder=None, tau=0.75)
    assert not ok


def test_slot_iri_exact_match_despite_different_names():
    # same OBO id, different surface names → IRI tier wins
    a = term("estradiol", "http://purl.obolibrary.org/obo/CHEBI_16469")
    b = term("17b-estradiol", "http://purl.obolibrary.org/obo/CHEBI_16469")
    ok, sim, method = match_slot(a, b, is_action=False, embedder=None, tau=0.75)
    assert ok and method == "iri" and sim == 1.0


def test_slot_name_exact_match():
    ok, _, method = match_slot(term("DNA"), term("dna"),
                               is_action=False, embedder=None, tau=0.75)
    assert ok and method == "name"


def test_slot_both_absent_is_compatible():
    ok, sim, method = match_slot(None, None, is_action=False, embedder=None, tau=0.75)
    assert ok and method == "absent"


def test_slot_one_absent_not_match():
    ok, _, _ = match_slot(term("DNA"), None, is_action=False, embedder=None, tau=0.75)
    assert not ok


def test_slot_embedding_above_tau():
    emb = StubEmbedder({frozenset(("hepatocyte", "liver cell")): 0.92})
    ok, sim, method = match_slot(term("hepatocyte"), term("liver cell"),
                                 is_action=False, embedder=emb, tau=0.75)
    assert ok and method == "embedding" and sim == 0.92


def test_slot_embedding_below_tau():
    emb = StubEmbedder({frozenset(("hepatocyte", "neuron")): 0.40})
    ok, _, method = match_slot(term("hepatocyte"), term("neuron"),
                               is_action=False, embedder=emb, tau=0.75)
    assert not ok and method == "embedding"


def test_slot_judge_resolves_borderline():
    # cosine 0.6 is in the grey band [tau-0.25, tau) = [0.5, 0.75); judge says equivalent
    emb = StubEmbedder({frozenset(("ROS", "reactive oxygen species")): 0.60})
    judge = lambda a, b: True
    ok, sim, method = match_slot(term("ROS"), term("reactive oxygen species"),
                                 is_action=False, embedder=emb, tau=0.75, judge=judge)
    assert ok and method == "judge"


def test_slot_judge_not_called_outside_band():
    # cosine 0.30 is below the grey band → judge must not flip it
    emb = StubEmbedder({frozenset(("a", "b")): 0.30})
    called = []
    def judge(x, y):
        called.append((x, y)); return True
    ok, _, method = match_slot(term("a"), term("b"),
                               is_action=False, embedder=emb, tau=0.75, judge=judge)
    assert not ok and method == "embedding" and called == []


# ── triple matching ───────────────────────────────────────────────────────────

def test_triple_requires_action_plus_one():
    pred = {"biological_object": term("DNA"),
            "biological_process": term("apoptosis"),
            "biological_action": term("increased")}
    ref = {"object": term("DNA"), "process": term("necrosis"), "action": term("increased")}
    # object matches (name), action matches; process differs → still a match
    matched, score, method = triple_match(pred, ref)
    assert matched and method == "name"


def test_triple_fails_on_action_mismatch():
    pred = {"biological_object": term("DNA"), "biological_action": term("increased")}
    ref = {"object": term("DNA"), "action": term("decreased")}
    matched, _, _ = triple_match(pred, ref)
    assert not matched


def test_triple_fails_when_only_action_matches():
    pred = {"biological_object": term("DNA"), "biological_action": term("increased")}
    ref = {"object": term("protein"), "action": term("increased")}
    matched, _, _ = triple_match(pred, ref)
    assert not matched  # action alone is not enough


# ── KE-level greedy assignment ────────────────────────────────────────────────

def test_match_ke_one_to_one():
    preds = [
        {"biological_object": term("DNA"), "biological_action": term("increased")},
        {"biological_object": term("DNA"), "biological_action": term("increased")},
    ]
    refs = [{"object": term("DNA"), "action": term("increased")}]  # only one ref
    res = match_ke(preds, refs)
    # exactly one prediction can consume the single reference
    assert sum(r["matched"] for r in res) == 1


def test_match_ke_picks_best_ref():
    preds = [{"biological_object": term("hepatocyte"),
              "biological_action": term("increased")}]
    refs = [
        {"object": term("neuron"), "action": term("increased")},
        {"object": term("hepatocyte"), "action": term("increased")},
    ]
    res = match_ke(preds, refs)
    assert res[0]["matched"] and res[0]["ref_idx"] == 1


# ── cross-model clustering / consensus ────────────────────────────────────────

def _tr(obj, act):
    return {"biological_object": term(obj), "biological_action": term(act)}


def test_cluster_agreeing_models_one_cluster():
    m2t = {
        "modelA": [_tr("DNA", "increased")],
        "modelB": [_tr("dna", "increased")],     # name variant → same cluster
        "modelC": [_tr("DNA", "increased")],
    }
    clusters = cluster_ke_triples(m2t)
    assert len(clusters) == 1
    assert sorted(clusters[0]["support"]) == ["modelA", "modelB", "modelC"]


def test_cluster_disagreeing_models_separate():
    m2t = {
        "modelA": [_tr("DNA", "increased")],
        "modelB": [_tr("protein", "decreased")],   # different object+action
    }
    clusters = cluster_ke_triples(m2t)
    assert len(clusters) == 2
    assert all(len(c["support"]) == 1 for c in clusters)


def test_cluster_support_counts_distinct_models():
    # a model emitting two equivalent triples still counts once toward support
    m2t = {
        "modelA": [_tr("DNA", "increased"), _tr("DNA", "increased")],
        "modelB": [_tr("DNA", "increased")],
    }
    clusters = cluster_ke_triples(m2t)
    assert len(clusters) == 1
    assert sorted(clusters[0]["support"]) == ["modelA", "modelB"]


def test_llm_triples_helper():
    assert llm_triples({"llm_extracted": {"triples": [1, 2]}}) == [1, 2]
    assert llm_triples({}) == []
    assert llm_triples({"llm_extracted": None}) == []
