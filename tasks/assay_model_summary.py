"""
assay_model_summary.py — Ploomber task: per-model assay metrics → one CSV row per model.

The assay analogue of model_summary.py. All metrics are GT-free. Runs in the consensus
stage and **loops over model_keys**, writing {products}/{model_key}/assay_model_summary.csv
for each (consumed by smaa_rank with summary_filename="assay_model_summary.csv"), plus a
combined CSV marker as the Ploomber product.

Ploomber:
  upstream: ["assay_logprob_calibration"]
  product:  {"csv": combined all-model CSV}
  params:   products, models, model_keys
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["assay_logprob_calibration"]
product = None
products = None
models = None
model_keys = None
# optional: {"json": ".../crossmodel_track_consistency.json"} from
# crossmodel_track_consistency (D5, response_plan.md — R1 §5.3). None → column left
# blank; kept opt-in rather than an implicit upstream dependency, since it only makes
# sense once both the KEC and assay tracks have been extracted for the same panel.
track_consistency_json = None
# -

logger = setup_logging(Path(product["csv"]).parent / "assay_model_summary.log")


def _read_json(path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


prod = Path(products)
xsum = _read_json(prod / "crossmodel_assays" / "crossmodel_summary.json")
per_model = (xsum.get("summary") or {}).get("per_model") or {}
tc_per_model = (_read_json(track_consistency_json).get("summary") or {}).get("per_model") or {} \
    if track_consistency_json else {}

rows = []
for mk in (model_keys or []):
    ev = _read_json(prod / mk / "assay_evidence_check.json")
    iri = _read_json(prod / mk / "validate_assay_iris.json")
    cal = _read_json(prod / mk / "assay_logprob_calibration.json")
    pm = per_model.get(mk, {})
    tc = tc_per_model.get(mk, {})
    n_assays = pm.get("n_assays") or ev.get("n_assays") or cal.get("n_assays") or 0

    row = {
        "model_key":              mk,
        "model_llm":              (models or {}).get(mk, {}).get("model_llm", ""),
        "n_ke":                   pm.get("n_ke"),
        "n_assays":               n_assays,
        "assays_per_ke":          (round(n_assays / pm["n_ke"], 2)
                                   if pm.get("n_ke") else None),
        # ── SMAA criteria (all higher-better, GT-free) ──
        "consensus_agreement":    pm.get("consensus_agreement"),
        "evidence_grounded_frac": ev.get("evidence_grounded_frac"),
        "iri_valid_frac":         iri.get("iri_valid_frac"),
        # separate population (kec_triple slots) — opt in via a ranking_config
        # that lists it in `criteria`; never folded into iri_valid_frac
        "kec_iri_valid_frac":     iri.get("kec_iri_valid_frac"),
        "direction_margin_auc":   cal.get("direction_margin_auc"),
        # D5 (response_plan.md, R1 §5.3): does this model's own assay-derived
        # kec_triple agree with its own KEC-track triple for the same KE? Primary
        # rule matches on object/process only (action excluded — see
        # crossmodel_track_consistency.py docstring); _strict additionally
        # requires the action slot to match too. Both blank unless
        # track_consistency_json is supplied (opt-in, see header).
        "cross_track_consistency":        tc.get("cross_track_consistency"),
        "cross_track_consistency_strict": tc.get("cross_track_consistency_strict"),
        # ── context ──
        "no_evidence_frac":       ev.get("no_evidence_frac"),
        "consensus_rate":         cal.get("consensus_rate"),
        "logprob_best_feature":   cal.get("best_feature"),
        "logprob_best_auc":       cal.get("best_auc"),
        "logprob_verdict":        cal.get("verdict"),
    }
    rows.append(row)
    # per-model CSV (what smaa_rank reads)
    (prod / mk).mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(prod / mk / "assay_model_summary.csv", index=False)
    logger.info(f"{mk}: n_assays={n_assays} "
                f"consensus={row['consensus_agreement']} "
                f"evidence={row['evidence_grounded_frac']} "
                f"iri={row['iri_valid_frac']} "
                f"kec_iri={row['kec_iri_valid_frac']} "
                f"dir_margin_auc={row['direction_margin_auc']}")

Path(product["csv"]).parent.mkdir(parents=True, exist_ok=True)
pd.DataFrame(rows).to_csv(product["csv"], index=False)
logger.info(f"wrote {len(rows)} model rows → {product['csv']}")
