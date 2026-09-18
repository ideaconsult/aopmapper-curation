"""
crossmodel_track_consistency.py — Ploomber task: per-model KEC-vs-assay cross-track
consistency (R1 §5.3 / response_plan.md D5).

Each model produces two INDEPENDENT LLM reads of the same Key Event: the KEC track
extracts an (object, process, action) triple from the KE *description*
(pipeline.kec.yaml → kec.json), and the assay track extracts a `kec_triple` in the same
shape from each assay's *measurement_methodology* text (pipeline.assays.yaml →
assays.json — see extract_assays.py). Neither is ground truth for the other, but if a
model is internally coherent, the biological change its assays claim to measure should
recognisably match the biological change its own KEC pass reported for that KE.

Matching rule — object/process only, action NOT required. tasks.kec_compare.match_ke
requires the action slot to match (the right rule for cross-MODEL agreement, where two
models both describing "decreased X" is meaningful corroboration). Cross-TRACK is a
different comparison: a single model's assay panel routinely reports BOTH directions for
the same underlying object/process (e.g. one assay shows T3 increased, another shows T3
decreased, depending what each assay actually measures), because each assay's action
comes from its own local methodology text, not the KE's canonical direction. A sample
check (200 KEs, one model) found action mismatch alone accounted for the majority of
"no match" cases under the strict rule, while object-or-process agreement was rarely the
problem — so it is object/process identity, not action, that shows the two tracks are
describing the same biology. `cross_track_consistency` (primary) therefore matches on
object-or-process only; `cross_track_consistency_strict` (secondary, kept alongside for
comparison) uses the full match_ke rule including action.

For each model, per KE present in BOTH products: match every assay-derived kec_triple
(greedy, 1:1) against that model's own KEC triples for the same KE, under both rules.
Each of `cross_track_consistency` / `_strict` = matched assay-triples ÷ assay-triples
with a non-null kec_triple, over KEs present in both tracks. This is a per-model, GT-free
signal — the assay analogue of `consensus_agreement`, except the second "voter" is the
model's own KEC pass rather than the rest of the panel — so it plugs into
assay_model_summary.csv / smaa_rank exactly like any other plain criterion (opt in via
a ranking_config block; NOT on by default, since it needs both tracks run for the same
panel+release to be meaningful).

Runs once in the crossmodel_assays_consensus stage (needs both product roots), unlike
assay_evidence_check.py which runs per-model. No Ploomber upstream on either side — both
kec.json and assays.json are read by path, like crossmodel_compare.py / crossmodel_assays.py.

Also writes a per-assay detail TSV (`detail`) — one row per scored assay: model, KE,
assay name, its own kec_triple, the KE's KEC-track triple(s), and whether each matched
under the primary (object/process) and strict (+action) rules — the curator-readable
audit trail behind the per-model `cross_track_consistency` number (which assays actually
agree, not just how many).

Ploomber:
  product:  {"json": summary, "tsv": per-model, "detail": per-assay TSV, "nb": notebook}
  params:   kec_products, assay_products, model_keys, embedding_model, tau
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from tasks.kec_compare import Embedder, llm_triples, match_slot, normalise_triple
from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = None                 # no DAG upstream — reads kec.json / assays.json by path
product = None
enabled = True
kec_products = None             # product root holding {model_key}/kec.json
assay_products = None           # product root holding {model_key}/assays.json
model_keys = None
embedding_model = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
tau = 0.75                      # KEC-track matching threshold (tasks.kec_compare.DEFAULT_TAU)
# -

logger = setup_logging(Path(product["json"]).parent / "crossmodel_track_consistency.log")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _assay_kec_triples(assays_for_ke: list) -> list[dict]:
    """(assay_name, triple) pairs from a KE's assays, triple normalised to the
    (object, process, action) shape match_ke expects; skips assays with no
    coherent biological change (kec_triple is null)."""
    out = []
    for a in assays_for_ke or []:
        t = a.get("kec_triple")
        if not t:
            continue
        out.append((a.get("assay_name"), {
            "object":  t.get("biological_object"),
            "process": t.get("biological_process"),
            "action":  t.get("biological_action"),
        }))
    return out


def _real(ok: bool, method: str) -> bool:
    """A slot 'matches' only on real evidence — both-absent is compatible, not positive."""
    return ok and method != "absent"


def _cross_track_match(pred: dict, ref: dict, *, embedder, tau: float) -> dict:
    """Score one (assay kec_triple, KEC triple) pair against BOTH matching rules.

    primary (object/process only — see module docstring for why action is excluded
    here): matched iff object OR process matches.
    strict (+action, tasks.kec_compare.triple_match's rule): matched iff action
    matches AND (object OR process matches).
    """
    p, r = normalise_triple(pred), normalise_triple(ref)
    obj_ok, obj_sim, obj_m = match_slot(p["object"],  r["object"],  is_action=False,
                                        embedder=embedder, tau=tau)
    prc_ok, prc_sim, prc_m = match_slot(p["process"], r["process"], is_action=False,
                                        embedder=embedder, tau=tau)
    act_ok, act_sim, act_m = match_slot(p["action"],  r["action"],  is_action=True,
                                        embedder=embedder, tau=tau)
    objproc_real = _real(obj_ok, obj_m) or _real(prc_ok, prc_m)
    act_real = _real(act_ok, act_m)
    return {
        "matched": objproc_real,
        "matched_strict": act_real and objproc_real,
        "score": (obj_sim + prc_sim) / 2.0,               # primary score: object/process only
        "score_strict": (obj_sim + prc_sim + act_sim) / 3.0,
        "method": obj_m if _real(obj_ok, obj_m) else (prc_m if _real(prc_ok, prc_m) else "none"),
    }


def _track_match_ke(pred_triples: list[dict], ref_triples: list[dict], *,
                    embedder, tau: float) -> list[dict]:
    """Greedy 1:1 match (mirrors tasks.kec_compare.match_ke), scored under both rules
    at once via _cross_track_match. Selection (which ref triple 'wins' per prediction,
    and reference-triple consumption) is driven by the PRIMARY (object/process) score,
    since that is the rule cross_track_consistency reports."""
    used: set = set()
    results = []
    for p in pred_triples:
        best = {"matched": False, "matched_strict": False, "score": 0.0,
                "score_strict": 0.0, "method": "none", "ref_idx": None}
        for j, r in enumerate(ref_triples):
            if j in used:
                continue
            m = _cross_track_match(p, r, embedder=embedder, tau=tau)
            if m["score"] > best["score"]:
                best = {**m, "ref_idx": j}
        if best["matched"] and best["ref_idx"] is not None:
            used.add(best["ref_idx"])
        results.append(best)
    return results


def _fmt_triple(t: dict | None) -> str:
    """'object / process / action' names, joined for one readable table cell.
    Accepts either key encoding (object/process/action OR biological_object/…,
    via kec_compare.normalise_triple) — the KEC track uses the biological_*
    keys, the assay side has already been remapped to the short ones. A triple
    with no populated slot at all renders as "" (not "— / — / —")."""
    if not t:
        return ""
    n = normalise_triple(t)
    slots = [(n.get(s) or {}).get("name") for s in ("object", "process", "action")]
    if not any(slots):
        return ""
    return " / ".join(s or "—" for s in slots)


if enabled:
    assert kec_products and assay_products and model_keys, \
        "params 'kec_products', 'assay_products', 'model_keys' are required"
    OUT_JSON = Path(product["json"])
    OUT_TSV = Path(product["tsv"])
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    kec_root = Path(kec_products)
    assay_root = Path(assay_products)
    embedder = Embedder(embedding_model)

    per_model = {}
    detail_rows = []       # KE, ASSAY, KE_KEC, ASSAY_KEC, matched — one row per scored assay
    for mk in model_keys:
        kec_path = kec_root / mk / "kec.json"
        assay_path = assay_root / mk / "assays.json"
        if not kec_path.exists() or not assay_path.exists():
            logger.warning(f"skip '{mk}': missing "
                           f"{'kec.json' if not kec_path.exists() else 'assays.json'}")
            continue
        kec = _load_json(kec_path)
        assays = _load_json(assay_path)

        common_kes = sorted(set(kec) & set(assays))
        n_matched = n_matched_strict = n_scored = n_ke_scored = 0
        per_ke = {}
        for kid in common_kes:
            kec_triples = llm_triples(kec.get(kid, {}))
            assay_pairs = _assay_kec_triples(assays.get(kid))
            if not kec_triples or not assay_pairs:
                continue
            n_ke_scored += 1
            assay_names = [name for name, _ in assay_pairs]
            assay_triples = [t for _, t in assay_pairs]
            results = _track_match_ke(assay_triples, kec_triples, embedder=embedder, tau=tau)
            n_matched += sum(1 for r in results if r["matched"])
            n_matched_strict += sum(1 for r in results if r["matched_strict"])
            n_scored += len(results)
            # Keyed by assay_name, not positional: assay_pairs is filtered to non-null
            # kec_triple and ordered by this model's own assays.json, which need not
            # match how any other consumer (e.g. viz_assay_consensus, whose clusters are
            # built by cross-model matching) orders or filters the same assays. A
            # positional per-KE list looked aligned but silently wasn't guaranteed to be
            # — this is what any future join against per_ke should use.
            per_ke[kid] = {name: r for name, r in zip(assay_names, results)}
            ke_kec_str = "; ".join(s for s in (_fmt_triple(t) for t in kec_triples) if s)
            for name, t, r in zip(assay_names, assay_triples, results):
                detail_rows.append({
                    "model_key": mk,
                    "ke": kid,
                    "assay": name,
                    "ke_kec": ke_kec_str,
                    "assay_kec": _fmt_triple(t),
                    "matched": r["matched"],
                    "matched_strict": r["matched_strict"],
                    "score": round(r["score"], 4),
                    "method": r["method"],
                })

        consistency = (n_matched / n_scored) if n_scored else None
        consistency_strict = (n_matched_strict / n_scored) if n_scored else None
        per_model[mk] = {
            "n_ke_common": len(common_kes),
            "n_ke_scored": n_ke_scored,          # common KEs where both sides had triples
            "n_assay_triples_scored": n_scored,
            "n_matched": n_matched,
            "n_matched_strict": n_matched_strict,
            "cross_track_consistency": consistency,               # primary: object/process only
            "cross_track_consistency_strict": consistency_strict,  # secondary: + action required
            "per_ke": per_ke,
        }
        logger.info(f"{mk}: {n_ke_scored}/{len(common_kes)} KEs scored, "
                    f"consistency={consistency} (strict={consistency_strict})")

    summary = {
        "kec_products": kec_products,
        "assay_products": assay_products,
        "models_requested": list(model_keys),
        "models_scored": [mk for mk, v in per_model.items() if v["n_assay_triples_scored"]],
        "tau": tau,
        "embedding_model": embedding_model,
        "matching_rule": "primary = object-or-process match (action not required); "
                         "strict = + action required (tasks.kec_compare.triple_match rule)",
        "per_model": {mk: {k: v for k, v in rec.items() if k != "per_ke"}
                      for mk, rec in per_model.items()},
    }
    OUT_JSON.write_text(json.dumps(
        {"summary": summary,
         "per_ke": {mk: rec["per_ke"] for mk, rec in per_model.items()}},
        indent=2, ensure_ascii=False), encoding="utf-8")

    with open(OUT_TSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["model_key", "n_ke_common", "n_ke_scored",
                    "n_assay_triples_scored", "n_matched", "n_matched_strict",
                    "cross_track_consistency", "cross_track_consistency_strict"])
        for mk, rec in per_model.items():
            w.writerow([mk, rec["n_ke_common"], rec["n_ke_scored"],
                        rec["n_assay_triples_scored"], rec["n_matched"], rec["n_matched_strict"],
                        rec["cross_track_consistency"], rec["cross_track_consistency_strict"]])

    # per-assay audit table: KE, ASSAY, KE_KEC, ASSAY_KEC, matched (primary/strict)?
    OUT_DETAIL = Path(product["detail"])
    with open(OUT_DETAIL, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["model_key", "ke", "assay", "ke_kec", "assay_kec",
                    "matched", "matched_strict", "score", "method"])
        for row in detail_rows:
            w.writerow([row["model_key"], row["ke"], row["assay"], row["ke_kec"],
                        row["assay_kec"], row["matched"], row["matched_strict"],
                        row["score"], row["method"]])

    logger.info(f"wrote {len(per_model)} model rows → {OUT_JSON}, {OUT_TSV}; "
                f"{len(detail_rows)} assay rows → {OUT_DETAIL}")
else:
    logger.info("crossmodel_track_consistency disabled — skipping")
