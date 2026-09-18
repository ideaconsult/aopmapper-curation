"""
model_summary.py — Ploomber task: collate per-model stats into a single CSV row.

Reads the CSV products written by the four upstream tasks and joins them into one
row written to {products}/{model_key}/model_summary.csv.

After all models are run, combine with:
    import pandas as pd, pathlib
    df = pd.concat([pd.read_csv(p) for p in
                    pathlib.Path("products_202606").glob("*/model_summary.csv")])

Ploomber:
  upstream: ["extract_kec", "compare_kec", "kec_logprob_calibration", "audit_kec_truth"]
  product:  {"csv": "...model_summary.csv"}
  params:   models, model_key
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["extract_kec", "compare_kec", "kec_logprob_calibration", "audit_kec_truth"]
product = None
models = None
model_key = None
# -

from tasks.logutil import setup_logging

OUT_CSV = Path(product["csv"])
OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
logger = setup_logging(OUT_CSV.parent / "model_summary.log")


def _read(path) -> dict:
    try:
        return pd.read_csv(path).iloc[0].to_dict()
    except Exception:
        return {}


ext  = _read(upstream["extract_kec"]["csv"])
cmp  = _read(upstream["compare_kec"]["csv"])
cal  = _read(upstream["kec_logprob_calibration"]["csv"])
aud  = _read(upstream["audit_kec_truth"]["csv"])

n_ke = ext.get("n_ke", 0) or 0
n_llm_triples = ext.get("n_llm_triples", 0) or 0

row = {
    "model_key":             model_key,
    "model_llm":             (models or {}).get(model_key, {}).get("model_llm", ""),
    # extraction
    "n_ke":                  n_ke,
    "n_llm_triples":         n_llm_triples,
    "n_llm_errors":          ext.get("n_llm_errors"),
    "triples_per_ke":        round(n_llm_triples / n_ke, 2) if n_ke else None,
    "elapsed_extract_s":     ext.get("elapsed_s"),
    # matching
    "match_rate":            cmp.get("match_rate"),
    "n_matched":             cmp.get("n_matched"),
    "n_pred_triples":        cmp.get("n_pred_triples"),
    "method_iri_frac":       cmp.get("method_iri_frac"),
    "method_name_frac":      cmp.get("method_name_frac"),
    "method_emb_frac":       cmp.get("method_emb_frac"),
    "elapsed_compare_s":     cmp.get("elapsed_s"),
    # logprob calibration
    "logprob_best_feature":  cal.get("best_feature"),
    "logprob_best_auc":      cal.get("best_auc"),
    "logprob_verdict":       cal.get("verdict"),
    "elapsed_logprob_s":     cal.get("elapsed_s"),
    # ground-truth audit
    "audit_correct_frac":    aud.get("audit_correct_frac"),
    "audit_partial_frac":    aud.get("audit_partial_frac"),
    "audit_wrong_frac":      aud.get("audit_wrong_frac"),
    "elapsed_audit_s":       aud.get("elapsed_s"),
}

pd.DataFrame([row]).to_csv(OUT_CSV, index=False)
logger.info(
    f"{model_key}: match_rate={row['match_rate']} "
    f"triples={n_llm_triples} errors={row['n_llm_errors']} "
    f"elapsed_extract={row['elapsed_extract_s']}s"
)
