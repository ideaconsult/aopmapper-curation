"""
assay_logprob_calibration.py — Ploomber task: does confidence predict panel corroboration?

The assay analogue of kec_logprob_calibration.py. With no ground truth, the correctness
**label is cross-model consensus membership** (an assay in a support≥min_support cluster).
The headline feature is the `endpoint_direction` margin — a controlled-vocabulary
classification confidence, like the KEC action margin — reported as a **sign-checked** AUC.

Because the label is cross-model, this runs in the consensus stage and **loops over all
models** internally (writing per-model JSON/TSV in each model dir, plus a combined marker).

Ploomber:
  upstream: ["crossmodel_assays"]
  product:  {"json": combined per-model summaries}
  params:   products, model_keys, min_support
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import numpy as np
from scipy.stats import pointbiserialr
from sklearn.metrics import roc_auc_score

from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["crossmodel_assays"]
product = None
enabled = True
products = None
model_keys = None
min_support = 2
# -

logger = setup_logging(Path(product["json"]).parent / "assay_logprob_calibration.log")

FEATURES = [
    "direction_margin",        # endpoint_direction top-logprobs margin (the star signal)
    "direction_logprob_mean",
    "name_logprob_mean",
    "method_type_margin",
    "mean_field_logprob",
]


def _features(rec: dict) -> dict:
    lp = rec.get("logprobs") or {}
    ed = lp.get("endpoint_direction") or {}
    mt = lp.get("method_type") or {}
    nm = lp.get("assay_name") or {}
    means = [v.get("logprob_mean") for v in lp.values()
             if isinstance(v, dict) and v.get("logprob_mean") is not None]
    return {
        "direction_margin":       ed.get("margin"),
        "direction_logprob_mean": ed.get("logprob_mean"),
        "name_logprob_mean":      nm.get("logprob_mean"),
        "method_type_margin":     mt.get("margin"),
        "mean_field_logprob":     (sum(means) / len(means)) if means else None,
    }


def _auc_directional(y, x):
    auc = roc_auc_score(y, x)
    return (auc, +1) if auc >= 0.5 else (1 - auc, -1)


def _calibrate_model(mk: str, consensus: dict, prod: Path) -> dict:
    apath = prod / mk / "assays.json"
    if not apath.exists():
        logger.warning(f"skip {mk}: {apath} not found")
        return {}
    assays_by_ke = json.loads(apath.read_text(encoding="utf-8"))

    positives = set()
    for ke_id, clusters in consensus.items():
        for c in clusters:
            if c.get("support", 0) < min_support:
                continue
            for m in c.get("members", []):
                if m.get("model") == mk and m.get("assay_idx") is not None:
                    positives.add((ke_id, m["assay_idx"]))

    rows = []
    for ke_id, assays in assays_by_ke.items():
        for i, rec in enumerate(assays or []):
            rows.append({"ke_id": ke_id, "assay_idx": i,
                         "correct": int((ke_id, i) in positives), **_features(rec)})

    cols = ["ke_id", "assay_idx", "correct", *FEATURES]
    with open(prod / mk / "assay_logprob_calibration.tsv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])

    metrics = {}
    for feat in FEATURES:
        pairs = [(r["correct"], r[feat]) for r in rows if r[feat] is not None]
        if len(pairs) < 10 or len({p[0] for p in pairs}) < 2:
            metrics[feat] = {"n": len(pairs), "note": "insufficient data / one class"}
            continue
        y = np.array([p[0] for p in pairs], dtype=float)
        x = np.array([p[1] for p in pairs], dtype=float)
        if x.std() == 0:
            metrics[feat] = {"n": len(pairs), "note": "degenerate"}
            continue
        auc, direction = _auc_directional(y, x)
        pb = pointbiserialr(y, x)
        metrics[feat] = {"n": len(pairs), "auc": round(auc, 4), "direction": direction,
                         "point_biserial_r": round(float(pb.statistic), 4),
                         "p_value": round(float(pb.pvalue), 4)}

    scored = {k: v for k, v in metrics.items() if "auc" in v}
    best = max(scored, key=lambda k: scored[k]["auc"], default=None)
    best_auc = scored[best]["auc"] if best else None
    dm = metrics.get("direction_margin", {})
    direction_margin_auc = ((dm.get("auc") if dm.get("direction", 0) > 0 else 0.5)
                            if "auc" in dm else None)
    n_pos = sum(r["correct"] for r in rows)
    summary = {
        "model_key": mk, "label": f"consensus_support>={min_support}",
        "n_assays": len(rows), "n_consensus": n_pos,
        "consensus_rate": (n_pos / len(rows)) if rows else None,
        "best_feature": best, "best_auc": best_auc,
        "direction_margin_auc": direction_margin_auc,
        "verdict": ("CARRY" if best_auc and best_auc >= 0.6
                    else "WEAK" if best_auc and best_auc >= 0.55
                    else "DROP" if best_auc else "INCONCLUSIVE"),
        "per_feature": metrics,
    }
    (prod / mk / "assay_logprob_calibration.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    # per-model stats CSV (parity with KEC)
    try:
        import pandas as pd
        pd.DataFrame([{"model_key": mk, "best_feature": best, "best_auc": best_auc,
                       "direction_margin_auc": direction_margin_auc,
                       "verdict": summary["verdict"]}]).to_csv(
            prod / mk / "assay_logprob_calibration_stats.csv", index=False)
    except Exception:
        pass
    return summary


if enabled:
    _t0 = time.perf_counter()
    prod = Path(products)
    consensus = json.loads(
        Path(upstream["crossmodel_assays"]["consensus"]).read_text(encoding="utf-8"))

    combined = {}
    for mk in (model_keys or []):
        s = _calibrate_model(mk, consensus, prod)
        if s:
            combined[mk] = s
            logger.info(f"{mk}: {s['n_consensus']}/{s['n_assays']} consensus; "
                        f"best={s['best_feature']} AUC={s['best_auc']} "
                        f"dir_margin_auc={s['direction_margin_auc']}")

    Path(product["json"]).parent.mkdir(parents=True, exist_ok=True)
    Path(product["json"]).write_text(
        json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"calibrated {len(combined)} models in {time.perf_counter()-_t0:.1f}s")
else:
    logger.info("assay_logprob_calibration disabled — skipping")
