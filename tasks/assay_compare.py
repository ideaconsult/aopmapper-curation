"""
assay_compare.py — hybrid assay matcher + cross-model clustering. LIBRARY, not a task.

The assay analogue of kec_compare.py. Assays have NO curated ground truth, so this is
used only for the GT-free signal: clustering equivalent assays ACROSS models per KE
(consensus). It reuses kec_compare's Embedder and tiered match_slot unchanged.

Assay signature (parallel to the KEC object/process/action triple):
  assay_name        — the identity slot   (IRI/name/embedding tiers)
  measured_endpoint — what is quantified  (IRI/name/embedding tiers)
  method_type       — controlled vocab    (exact only — the "action-like" slot)

Match rule: two assay records refer to the same assay iff assay_name matches AND their
measured_endpoints do not conflict. An endpoint conflicts only when BOTH sides state one
and they disagree; method_type never decides the match.

The rule is asymmetric between "disagrees" and "does not say", and deliberately so.
Placeholder values ("", "not_specified", …) are treated as absent, and absent is neutral:
a field the source text omits is missing information rather than evidence of a different
assay. The earlier rule required a positive endpoint OR method_type match, which made any
record with unstated fields unmatchable — and made method_type disagreement fatal, so the
same assay run in vivo in one Key Event and in vitro in another split into two clusters.
Both effects depressed support counts and inflated the apparent number of distinct assays.
"""
from __future__ import annotations

from typing import Optional

from tasks.kec_compare import Embedder, match_slot, DEFAULT_EMBEDDING_MODEL

# Assay names are short and acronymic, and the same assay is written many ways
# ("HPRT assay (OECD TG476)" / "HPRT Mutation Assay", "Cellular imaging (TEM)" /
# "transmission electron microscopy"). Set by inspecting a sample of pairs that should
# and should not match: true positives ran 0.70-1.00, true negatives 0.39-0.64, so 0.70
# sits in the gap. NOT fitted — an inspection, not a calibration.
DEFAULT_ASSAY_TAU = 0.70

_PLACEHOLDERS = {"", "not_specified", "not specified", "none", "n/a", "na", "unknown"}


def _term(name: Optional[str]) -> Optional[dict]:
    """Wrap a plain assay field value as a term dict, or None for placeholders."""
    if not name or not isinstance(name, str):
        return None
    if name.strip().lower() in _PLACEHOLDERS:
        return None
    return {"name": name}


def assay_match(a: dict, b: dict, *, embedder: Optional[Embedder] = None,
                tau: float = DEFAULT_ASSAY_TAU, judge=None) -> tuple[bool, float, str]:
    """Return (matched, score, method) for two assay records.

    Rule: assay_name must match; the match then stands unless measured_endpoint is
    present on BOTH sides and disagrees. method_type is reported but never decides:
    it records how an assay was applied (in vitro / in vivo), not which assay it is.
    An absent slot is neutral — missing information, not disagreement.
    """
    name_ok, name_sim, name_m = match_slot(
        _term(a.get("assay_name")), _term(b.get("assay_name")),
        is_action=False, embedder=embedder, tau=tau, judge=judge)
    ep_ok, ep_sim, ep_m = match_slot(
        _term(a.get("measured_endpoint")), _term(b.get("measured_endpoint")),
        is_action=False, embedder=embedder, tau=tau, judge=judge)
    _, mt_sim, _ = match_slot(
        _term(a.get("method_type")), _term(b.get("method_type")),
        is_action=True, embedder=embedder, tau=tau)

    def _real(ok, m):
        """Positive evidence: the slot matched AND both sides actually had a value."""
        return ok and m != "absent"

    score = (name_sim + ep_sim + mt_sim) / 3.0

    # The identity slot is mandatory: without agreeing names there is no assay to compare.
    if not _real(name_ok, name_m):
        return False, score, "none"

    # Given agreeing names, the only thing that can now REFUTE the match is a
    # measured_endpoint present on both sides and disagreeing — two records naming the
    # same assay but quantifying different things really are different assays.
    #
    # Everything else is not evidence against:
    #  * method_type disagreement is non-fatal. The same assay is legitimately run
    #    in vitro in one Key Event and in vivo in another (a transgenic rodent mutation
    #    assay against its in vitro counterpart), so this records how the assay was
    #    applied, not which assay it is.
    #  * an absent slot is neutral, not negative. A field the source text never states
    #    is missing information; scoring it as disagreement would penalise records for
    #    what their source omits, and would make a name-only reference fixture
    #    (resources/aop451_reference.json) unmatchable by construction.
    endpoints_conflict = (ep_m != "absent") and not ep_ok
    return (not endpoints_conflict), score, name_m


def cluster_ke_assays(model_to_assays: dict, *, embedder: Optional[Embedder] = None,
                      tau: float = DEFAULT_ASSAY_TAU, judge=None) -> list[dict]:
    """Cluster equivalent assays ACROSS models for a single KE (consensus primitive).

    model_to_assays : {model_key: [assay_record, ...]}
    Returns clusters, each:
      {"representative": <assay>, "members": [(model_key, idx), ...],
       "support": [model_key, ...]}
    Greedy single-link, mirroring kec_compare.cluster_ke_triples.
    """
    clusters: list[dict] = []
    for model, assays in model_to_assays.items():
        for idx, rec in enumerate(assays):
            placed = False
            for c in clusters:
                matched, _, _ = assay_match(rec, c["representative"],
                                            embedder=embedder, tau=tau, judge=judge)
                if matched:
                    c["members"].append((model, idx))
                    if model not in c["support"]:
                        c["support"].append(model)
                    placed = True
                    break
            if not placed:
                clusters.append({"representative": rec, "members": [(model, idx)],
                                 "support": [model]})
    return clusters


def assay_list(rec) -> list:
    """The list of assay records for a KE from assays.json ({ke: [assay, ...]})."""
    return rec if isinstance(rec, list) else []
