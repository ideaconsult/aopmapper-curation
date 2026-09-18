"""
kec_logprob_calibration.py — Ploomber task: does the model's own confidence
predict whether an extracted triple is actually correct?

This is the logprob "go/no-go" gate (spec §4.4). For every predicted triple on a
KE that has curated ground truth, we:
  1. label it correct/incorrect via the hybrid matcher (tasks/kec_compare.py), and
  2. read its logprob features (already attached by extract_kec.attach_logprobs):
       - triple_logprob_mean : mean of the slots' logprob_mean
       - action_logprob_mean : confidence of the controlled-vocab action token
       - action_margin       : rank1 − rank2 of the action top_logprobs (class margin)
       - min_slot_logprob    : weakest slot
       - response_mean / response_perplexity : whole-answer confidence

Then we report, per feature, AUC and point-biserial correlation vs correctness, plus a
reliability table. Decision: AUC ≈ 0.5 ⇒ logprobs don't predict KEC correctness ⇒ drop
Tier-1 from the quality model; AUC clearly > 0.5 ⇒ keep (action_margin likely leads).

Ploomber:
  upstream: ["extract_kec"]
  product:  {"tsv": per-triple features+label, "json": metrics summary}
  params:   embedding_model, tau, use_embeddings
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
from scipy.stats import pointbiserialr
from sklearn.metrics import roc_auc_score

from tasks.kec_compare import Embedder, iter_annotated, match_ke
from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["extract_kec"]
product = None
enabled = True
model_key = None
embedding_model = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
tau = 0.75
use_embeddings = True
# -

KEC_PATH = upstream["extract_kec"]["json"]
logger = setup_logging(Path(product["json"]).parent / "kec_logprob_calibration.log")

FEATURES = [
    "triple_logprob_mean",
    "action_logprob_mean",
    "action_margin",
    "min_slot_logprob",
    "response_mean",
    "response_perplexity",
]


def _slot_logprobs(triple: dict) -> list[float]:
    out = []
    for slot in ("biological_object", "biological_process", "biological_action"):
        term = triple.get(slot)
        if term and term.get("logprob_mean") is not None:
            out.append(term["logprob_mean"])
    return out


def _action_margin(triple: dict):
    """rank1 − rank2 logprob of the action's top_logprobs (≥0; larger = more confident)."""
    act = triple.get("biological_action") or {}
    top = act.get("top_logprobs") or []
    if len(top) >= 2:
        return top[0]["logprob"] - top[1]["logprob"]
    return None


def _triple_features(triple: dict, resp: dict) -> dict:
    slot_lps = _slot_logprobs(triple)
    act = triple.get("biological_action") or {}
    resp = resp or {}
    return {
        "triple_logprob_mean": (sum(slot_lps) / len(slot_lps)) if slot_lps else None,
        "action_logprob_mean": act.get("logprob_mean"),
        "action_margin":       _action_margin(triple),
        "min_slot_logprob":    min(slot_lps) if slot_lps else None,
        "response_mean":       resp.get("mean"),
        "response_perplexity": resp.get("perplexity"),
    }


def _auc_directional(y: np.ndarray, x: np.ndarray):
    """ROC-AUC of feature x predicting label y, oriented so AUC≥0.5.
    Returns (auc, direction) where direction=+1 means higher x ⇒ more likely correct."""
    auc = roc_auc_score(y, x)
    if auc < 0.5:
        return 1 - auc, -1
    return auc, +1


def _reliability(y: np.ndarray, x: np.ndarray, bins: int = 4) -> list[dict]:
    """Quantile-bin x; report empirical accuracy per bin (monotone ⇒ predictive)."""
    order = np.argsort(x)
    chunks = np.array_split(order, bins)
    table = []
    for i, idx in enumerate(chunks):
        if len(idx) == 0:
            continue
        table.append({
            "bin": i,
            "x_min": float(x[idx].min()),
            "x_max": float(x[idx].max()),
            "n": int(len(idx)),
            "accuracy": float(y[idx].mean()),
        })
    return table


if enabled:
    import time
    _t0 = time.perf_counter()
    OUT_TSV  = Path(product["tsv"])
    OUT_JSON = Path(product["json"])
    OUT_TSV.parent.mkdir(parents=True, exist_ok=True)

    with open(KEC_PATH, encoding="utf-8") as f:
        kec = json.load(f)

    has_logprobs = any(
        (rec.get("llm_extracted") or {}).get("_response_logprob")
        or any(
            (t.get("biological_action") or {}).get("top_logprobs")
            or (t.get("biological_object") or {}).get("logprob_mean") is not None
            for t in (rec.get("llm_extracted") or {}).get("triples", [])
        )
        for rec in kec.values()
    )
    if not has_logprobs:
        logger.warning(
            "No logprob data found in kec.json — calibration skipped "
            "(model was run with logprobs=false or does not support them)"
        )
        import pandas as pd
        pd.DataFrame([{
            "model_key": model_key, "elapsed_s": 0,
            "best_feature": None, "best_auc": None, "verdict": "no_logprobs",
        }]).to_csv(product["csv"], index=False)
        # write empty outputs so downstream tasks don't fail on missing files
        Path(product["tsv"]).write_text("ke_id\ttriple_idx\tcorrect\n", encoding="utf-8")
        import json as _json
        Path(product["json"]).write_text(
            _json.dumps({"summary": {"verdict": "no_logprobs"}, "rows": []}),
            encoding="utf-8",
        )
    if has_logprobs:
        embedder = Embedder(embedding_model) if use_embeddings else None

        rows = []
        for ke_id, structured, llm in iter_annotated(kec):
            resp = (kec[ke_id].get("llm_extracted") or {}).get("_response_logprob") or {}
            matches = match_ke(llm, structured, embedder=embedder, tau=tau)
            for i, (triple, m) in enumerate(zip(llm, matches)):
                feats = _triple_features(triple, resp)
                rows.append({"ke_id": ke_id, "triple_idx": i,
                             "correct": int(m["matched"]), **feats})

        # ── write per-triple TSV ──────────────────────────────────────────────
        cols = ["ke_id", "triple_idx", "correct", *FEATURES]
        with open(OUT_TSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(cols)
            for r in rows:
                w.writerow([r.get(c, "") for c in cols])

        # ── per-feature metrics ───────────────────────────────────────────────
        y_all = np.array([r["correct"] for r in rows])
        n_pos, n_neg = int(y_all.sum()), int((1 - y_all).sum())
        metrics = {}
        for feat in FEATURES:
            pairs = [(r["correct"], r[feat]) for r in rows if r[feat] is not None]
            if len(pairs) < 10 or len({p[0] for p in pairs}) < 2:
                metrics[feat] = {"n": len(pairs), "note": "insufficient data / one class"}
                continue
            y = np.array([p[0] for p in pairs], dtype=float)
            x = np.array([p[1] for p in pairs], dtype=float)
            if len({p[0] for p in pairs}) < 2 or x.std() == 0:
                metrics[feat] = {"n": len(pairs), "note": "degenerate"}
                continue
            auc, direction = _auc_directional(y, x)
            pb = pointbiserialr(y, x)
            metrics[feat] = {
                "n": len(pairs),
                "auc": round(auc, 4),
                "direction": direction,
                "point_biserial_r": round(float(pb.statistic), 4),
                "p_value": round(float(pb.pvalue), 4),
                "reliability": _reliability(y, x),
            }

        # best feature + carry/drop verdict
        scored = {k: v for k, v in metrics.items() if "auc" in v}
        best = max(scored, key=lambda k: scored[k]["auc"], default=None)
        best_auc = scored[best]["auc"] if best else None
        verdict = ("CARRY logprob features (predictive)" if best_auc and best_auc >= 0.6
                   else "WEAK — consider dropping Tier-1" if best_auc and best_auc >= 0.55
                   else "DROP logprob features (≈chance)" if best_auc
                   else "INCONCLUSIVE (insufficient data)")

        summary = {
            "n_triples": len(rows), "n_correct": n_pos, "n_incorrect": n_neg,
            "match_rate": (n_pos / len(rows)) if rows else None,
            "tau": tau, "embedding_model": embedding_model if use_embeddings else None,
            "best_feature": best, "best_auc": best_auc, "verdict": verdict,
            "per_feature": metrics,
        }
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        logger.info(f"Triples: {len(rows)}  correct={n_pos}  incorrect={n_neg}")
        for feat in FEATURES:
            mv = metrics[feat]
            if "auc" in mv:
                logger.info(f"  {feat:<22} AUC={mv['auc']:.3f} dir={mv['direction']:+d} "
                            f"r={mv['point_biserial_r']:+.3f} p={mv['p_value']:.3g}")
            else:
                logger.info(f"  {feat:<22} {mv.get('note')}")
        logger.info(f"VERDICT: {verdict}  (best={best} AUC={best_auc})")

        amp = [(r["correct"], r["action_margin"]) for r in rows
               if r.get("action_margin") is not None]
        if len(amp) >= 10 and len({c for c, _ in amp}) == 2:
            try:
                from tasks.figviz import auc_explainer
                info = auc_explainer(
                    [c for c, _ in amp], [s for _, s in amp],
                    model=Path(OUT_JSON).parent.name, feature="action-margin",
                    out_path=OUT_JSON.parent / "fig_auc_explainer.png")
                logger.info(f"AUC explainer figure → {info['out']} (AUC={info['auc']:.3f})")
            except Exception as e:
                logger.warning(f"AUC explainer figure skipped: {e}")
        else:
            logger.info("AUC explainer figure skipped: insufficient labelled data")
        try:
            import pandas as pd
            pd.DataFrame([{
                "model_key":    model_key,
                "elapsed_s":    round(time.perf_counter() - _t0, 1),
                "best_feature": best,
                "best_auc":     best_auc,
                "verdict":      verdict,
            }]).to_csv(product["csv"], index=False)
        except Exception as _e:
            logger.warning(f"stats CSV write failed: {_e}")
else:
    logger.info("kec_logprob_calibration disabled — skipping")
