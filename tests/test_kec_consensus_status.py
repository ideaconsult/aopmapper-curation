"""Tests for the derived statuses of the KEC consensus export (tasks/kec_xml.py): a port of
the confirmed / missed / novel logic of the viz_consensus renderer, plus the acceptance
threshold. Inline fixtures in the shape of viz_consensus.json, no pipeline products."""
import pytest

from tasks.kec_xml import best_match, derive_status


def mem(model="m1", matched=None, ref=None, method="name", score=0.8):
    """A cluster member; `matched=None` = no match record at all."""
    m = {"model": model, "slots": {}, "match": None}
    if matched is not None:
        m["match"] = {"matched": matched, "method": method, "score": score, "ref_idx": ref}
    return m


def cluster(support, *members):
    return {"support": support, "models": [], "rep": {}, "members": list(members)}


def ke(n_gt, *clusters):
    return {"ke_id": "KE1", "gt": [{} for _ in range(n_gt)], "clusters": list(clusters)}


# ── confirmed / missed / novel ────────────────────────────────────────────────

def test_cluster_with_a_matched_member_confirms_its_curated_triple():
    st = derive_status(ke(2, cluster(3, mem(matched=True, ref=1))))
    assert st.curated == ["missed", "confirmed"]
    assert st.refs == {0: [1]} and st.cluster_status(0) == "confirmed"


def test_cluster_with_no_match_is_novel_and_every_curated_triple_missed():
    st = derive_status(ke(2, cluster(3, mem(), mem(matched=False, ref=0))))
    assert st.curated == ["missed", "missed"]
    assert st.cluster_status(0) == "novel"


def test_unmatched_member_with_a_reference_does_not_confirm():
    """kec_match_report keeps the nearest curated triple (ref_idx) even when matched is
    false; only matched ones count."""
    st = derive_status(ke(1, cluster(2, mem(matched=False, ref=0))))
    assert st.curated == ["missed"] and st.refs == {0: []}


def test_matched_without_reference_is_novel_like_the_viz_report():
    """The renderer needs ref_idx != null to link a cluster to a curated triple."""
    st = derive_status(ke(1, cluster(2, mem(matched=True, ref=None))))
    assert st.curated == ["missed"] and st.cluster_status(0) == "novel"


def test_one_cluster_can_confirm_several_curated_triples():
    st = derive_status(ke(3, cluster(4, mem(matched=True, ref=2), mem(matched=True, ref=0),
                                     mem(matched=True, ref=2))))
    assert st.refs[0] == [0, 2]                      # sorted, de-duplicated
    assert st.curated == ["confirmed", "missed", "confirmed"]


def test_several_clusters_confirming_the_same_triple_is_a_tie_not_a_conflict():
    st = derive_status(ke(1, cluster(5, mem(matched=True, ref=0)),
                          cluster(5, mem(matched=True, ref=0)),
                          cluster(2, mem())))
    assert st.curated == ["confirmed"]
    assert [st.cluster_status(i) for i in st.accepted] == ["confirmed", "confirmed", "novel"]


def test_key_event_without_curated_triples_has_only_novel_clusters():
    st = derive_status(ke(0, cluster(3, mem(matched=True, ref=0))))
    assert st.curated == [] and st.n_out_of_range == 1
    assert st.cluster_status(0) == "novel"


def test_reference_past_the_curated_triples_is_ignored_and_counted():
    st = derive_status(ke(2, cluster(3, mem(matched=True, ref=5),
                                     mem(matched=True, ref=-1), mem(matched=True, ref=1))))
    assert st.refs[0] == [1] and st.n_out_of_range == 2


def test_empty_key_event():
    st = derive_status({"ke_id": "KE1"})
    assert st.accepted == [] and st.curated == [] and st.n_below == 0


# ── acceptance threshold ──────────────────────────────────────────────────────

def test_clusters_below_the_threshold_are_not_accepted_and_confirm_nothing():
    k = ke(2, cluster(1, mem(matched=True, ref=0)), cluster(2, mem(matched=True, ref=1)),
           cluster(3, mem()))
    st = derive_status(k, min_support=2)
    assert st.accepted == [1, 2] and st.n_below == 1
    assert st.curated == ["missed", "confirmed"]     # triple 0 was matched only below it
    assert 0 not in st.refs
    low = derive_status(k, min_support=1)
    assert low.accepted == [0, 1, 2] and low.curated == ["confirmed", "confirmed"]


def test_threshold_is_inclusive():
    st = derive_status(ke(1, cluster(2, mem(matched=True, ref=0))), min_support=2)
    assert st.curated == ["confirmed"]


# ── best match shown on the cluster (viz_consensus clusterSummary) ────────────

@pytest.mark.parametrize("members, expected", [
    ([], (None, None)),
    ([mem(), mem(matched=False, ref=0, method="none", score=0.4)], (None, None)),
    # the first matched member sets it ...
    ([mem(matched=True, ref=0, method="embedding", score=0.6),
      mem(matched=True, ref=0, method="name", score=0.9)], ("embedding", 0.6)),
    # ... an iri match replaces it, and so does every later iri match
    ([mem(matched=True, ref=0, method="embedding", score=0.6),
      mem(matched=True, ref=0, method="iri", score=1.0),
      mem(matched=True, ref=0, method="name", score=0.9),
      mem(matched=True, ref=0, method="iri", score=0.95)], ("iri", 0.95)),
    # unmatched members never set it
    ([mem(matched=False, ref=0, method="iri", score=1.0),
      mem(matched=True, ref=0, method="judge", score=0.7)], ("judge", 0.7)),
])
def test_best_match_follows_the_viz_report(members, expected):
    assert best_match(cluster(2, *members)) == expected
