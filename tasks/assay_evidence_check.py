"""
assay_evidence_check.py — Ploomber task: judge-free faithfulness of extracted assays.

Assays have no curated ground truth, but each extracted assay carries `evidence` —
verbatim span(s) the model claims to have copied from the measurement_methodology text.
This task verifies that claim: an assay is *grounded* if its evidence spans are actually
present in the cleaned source text (normalised substring, or high token-overlap fallback).

A hallucinated assay typically cites evidence that is NOT in the source, so the per-model
`evidence_grounded_frac` is a cheap, model-free quality signal — one input to the SMAA
model ranking and a flag for curator review.

Ploomber:
  upstream: ["extract_assays", "parse_pydantic"]
  product:  {"json": summary, "tsv": per-assay, "csv": stats}
  params:   model_key, min_span_frac, overlap_tau
"""
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path

from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["extract_assays", "parse_pydantic"]
product = None
enabled = True
model_key = None
min_span_frac = 0.5     # assay grounded if ≥ this fraction of its evidence spans found
overlap_tau = 0.85      # token-overlap threshold for the fuzzy fallback
# -

logger = setup_logging(Path(product["json"]).parent / "assay_evidence_check.log")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (t or "").lower())).strip()


def _clean_html(text: str) -> str:
    try:
        from tasks.utils import clean_html
        return clean_html(text)
    except ImportError:
        return re.sub(r"<[^>]+>", " ", text or "").replace("\xa0", " ").strip()


def _find(span: str, source_norm: str, source_tokens: set) -> str:
    """Return 'substring' | 'overlap' | 'none' for one evidence span."""
    sp = _norm(span)
    if not sp:
        return "none"
    if sp in source_norm:
        return "substring"
    toks = sp.split()
    if toks:
        hit = sum(1 for w in toks if w in source_tokens) / len(toks)
        if hit >= overlap_tau:
            return "overlap"
    return "none"


def _load_sources() -> dict:
    """wiki_id ('KE####') -> cleaned measurement_methodology text (the grounding corpus).

    Keyed by wiki_id, not the raw UUID, because assays_by_ke (extract_assays.json) is
    keyed by wiki_id end-to-end (see extract_assays.py's build_wiki_id_map usage).
    """
    from aopkb import Data
    from tasks.utils import preprocess_timestamps
    from tasks.aop_select import build_wiki_id_map
    with open(upstream["parse_pydantic"]["json"], encoding="utf-8") as f:
        raw = json.load(f)
    preprocess_timestamps(raw)
    data = Data.model_validate(raw)
    wiki_map = build_wiki_id_map(data)
    out = {}
    for ke in data.key_event:
        mm = getattr(ke, "measurement_methodology", None) or ""
        wiki_id = wiki_map.get(ke.id, str(ke.id))
        out[wiki_id] = _clean_html(mm)
    return out


if enabled:
    _t0 = time.perf_counter()
    OUT_JSON = Path(product["json"])
    OUT_TSV = Path(product["tsv"])
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    with open(upstream["extract_assays"]["json"], encoding="utf-8") as f:
        assays_by_ke = json.load(f)
    sources = _load_sources()

    rows = []
    n_assays = n_with_ev = n_grounded = 0
    for ke_id, assays in assays_by_ke.items():
        src = sources.get(ke_id, "")
        src_norm = _norm(src)
        src_tokens = set(src_norm.split())
        for i, rec in enumerate(assays or []):
            ev = rec.get("evidence") or []
            n_assays += 1
            methods = [_find(s, src_norm, src_tokens) for s in ev]
            n_found = sum(1 for m in methods if m != "none")
            has_ev = len(ev) > 0
            grounded = bool(has_ev and src_norm and
                            (n_found / len(ev)) >= min_span_frac)
            n_with_ev += int(has_ev)
            n_grounded += int(grounded)
            rows.append({
                "ke_id": ke_id, "assay_idx": i,
                "assay_name": rec.get("assay_name", ""),
                "n_evidence": len(ev), "n_found": n_found,
                "grounded": int(grounded),
                "method": ("substring" if "substring" in methods
                           else "overlap" if "overlap" in methods
                           else ("none" if has_ev else "no_evidence")),
            })

    with open(OUT_TSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["ke_id", "assay_idx", "assay_name", "n_evidence",
                    "n_found", "grounded", "method"])
        for r in rows:
            w.writerow([r[k] for k in ("ke_id", "assay_idx", "assay_name",
                                       "n_evidence", "n_found", "grounded", "method")])

    summary = {
        "model_key": model_key,
        "n_assays": n_assays,
        "n_with_evidence": n_with_ev,
        "n_grounded": n_grounded,
        "evidence_grounded_frac": (n_grounded / n_assays) if n_assays else None,
        "grounded_given_evidence": (n_grounded / n_with_ev) if n_with_ev else None,
        "no_evidence_frac": ((n_assays - n_with_ev) / n_assays) if n_assays else None,
        "min_span_frac": min_span_frac, "overlap_tau": overlap_tau,
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    try:
        import pandas as pd
        pd.DataFrame([{
            "model_key": model_key,
            "elapsed_s": round(time.perf_counter() - _t0, 1),
            "n_assays": n_assays,
            "evidence_grounded_frac": summary["evidence_grounded_frac"],
            "no_evidence_frac": summary["no_evidence_frac"],
        }]).to_csv(product["csv"], index=False)
    except Exception as e:
        logger.warning(f"stats CSV write failed: {e}")

    logger.info(f"{model_key}: {n_grounded}/{n_assays} assays grounded "
                f"({summary['evidence_grounded_frac']}), "
                f"no-evidence {summary['no_evidence_frac']}")
else:
    logger.info("assay_evidence_check disabled — skipping")
