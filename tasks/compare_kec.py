"""
compare_kec.py — Ploomber task: match LLM-extracted KEC triples to the curated
XML ground truth using the hybrid matcher (tasks/kec_compare.py).

Replaces the schema-stale `diagnose_triple_matching.py`: it reports, per model run,
how many extracted triples match the structured ground truth and via which tier
(IRI / name / embedding). This is the foundation for the quality model's
triple-level correctness labels (spec §5.3) and cross-model consensus (§7.4).

Ploomber:
  upstream: ["extract_kec"]
  product:  {"json": "...match report...", "tsv": "...per-triple..."}
  params:   embedding_model, tau, use_embeddings
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from tasks.kec_compare import Embedder, match_report, iter_annotated, match_ke
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

KEC_PATH  = upstream["extract_kec"]["json"]
OUT_JSON  = Path(product["json"])
OUT_TSV   = Path(product["tsv"])

logger = setup_logging(OUT_JSON.parent / "compare_kec.log")


def _write_tsv(kec: dict, per_ke: dict, path: Path) -> None:
    """One row per predicted triple with its match verdict and the slot names."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["ke_id", "triple_idx", "matched", "score", "method", "ref_idx",
                    "obj_name", "proc_name", "act_name"])
        for ke_id, _structured, llm in iter_annotated(kec):
            for i, (triple, m) in enumerate(zip(llm, per_ke.get(ke_id, []))):
                obj  = (triple.get("biological_object")  or {}).get("name", "")
                proc = (triple.get("biological_process") or {}).get("name", "")
                act  = (triple.get("biological_action")  or {}).get("name", "")
                w.writerow([ke_id, i, int(m["matched"]), round(m["score"], 4),
                            m["method"], m["ref_idx"], obj, proc, act])


if enabled:
    import time
    _t0 = time.perf_counter()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    with open(KEC_PATH, encoding="utf-8") as f:
        kec = json.load(f)

    embedder = Embedder(embedding_model) if use_embeddings else None
    report = match_report(kec, embedder=embedder, tau=tau)
    report["summary"]["embedding_model"] = embedding_model if use_embeddings else None

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    _write_tsv(kec, report["per_ke"], OUT_TSV)

    s = report["summary"]
    logger.info(
        f"Matched {s['n_matched']}/{s['n_pred_triples']} triples "
        f"({(s['match_rate'] or 0)*100:.1f}%) across {s['n_ke']} KEs "
        f"| tiers={s['method_counts']}"
    )

    # per-model assessment figure: match-tier breakdown, in this model's product dir
    if s["n_pred_triples"]:
        try:
            from tasks.figviz import match_tiers
            info = match_tiers(s["method_counts"], model=OUT_JSON.parent.name,
                               n_pred=s["n_pred_triples"], n_matched=s["n_matched"],
                               out_path=OUT_JSON.parent / "fig_match_tiers.png")
            logger.info(f"match-tier figure → {info['out']}")
        except Exception as e:
            logger.warning(f"match-tier figure skipped: {e}")
    try:
        import pandas as pd
        _s = report["summary"]
        _mc = _s.get("method_counts", {})
        _np = _s.get("n_pred_triples") or 1
        pd.DataFrame([{
            "model_key":         model_key,
            "elapsed_s":         round(time.perf_counter() - _t0, 1),
            "n_pred_triples":    _s.get("n_pred_triples"),
            "n_matched":         _s.get("n_matched"),
            "match_rate":        _s.get("match_rate"),
            "method_iri_frac":   round(_mc.get("iri", 0) / _np, 4),
            "method_name_frac":  round(_mc.get("name", 0) / _np, 4),
            "method_emb_frac":   round(_mc.get("embedding", 0) / _np, 4),
        }]).to_csv(product["csv"], index=False)
    except Exception as _e:
        logger.warning(f"stats CSV write failed: {_e}")
else:
    logger.info("compare_kec disabled — skipping")
