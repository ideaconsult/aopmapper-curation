"""
usage_stats.py — Ploomber task: per-model prompt-length / token-usage statistics.

Reads each model's logprob sidecar (kec_logprobs.jsonl for the KEC track,
assays_logprobs.jsonl for the assay track), which now carries per-KE usage fields
(`prompt_chars` always; `prompt_tokens`/`completion_tokens`/`total_tokens` when the
backend returns a `usage` object), and aggregates them into one row per model.

Decoupled from extraction on purpose: re-run any time without re-querying the models.
Streams the sidecar line by line (memory-safe even for large token arrays).

Ploomber:
  product:  {"csv": combined per-model usage table}
  params:   products (model-dir root), model_keys, sidecar_name
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
product = None
enabled = True
products = None
model_keys = None
sidecar_name = "kec_logprobs.jsonl"   # assay track: "assays_logprobs.jsonl"
# -

logger = setup_logging(Path(product["csv"]).parent / "usage_stats.log")

_FIELDS = ("prompt_chars", "prompt_tokens", "completion_tokens", "total_tokens")


def _collect(path: Path) -> dict:
    """Stream one sidecar; return {field: [values...]} (Nones dropped)."""
    cols: dict = {f: [] for f in _FIELDS}
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            n += 1
            for fld in _FIELDS:
                v = rec.get(fld)
                if isinstance(v, (int, float)):
                    cols[fld].append(float(v))
    cols["_n_records"] = n
    return cols


def _stats(model_key: str, cols: dict) -> dict:
    n = cols["_n_records"]
    pc = np.array(cols["prompt_chars"], dtype=float)
    pt = np.array(cols["prompt_tokens"], dtype=float)
    ct = np.array(cols["completion_tokens"], dtype=float)
    tt = np.array(cols["total_tokens"], dtype=float)

    def q(a, fn):
        return round(float(fn(a)), 1) if a.size else None

    return {
        "model_key": model_key,
        "n_records": n,
        "n_with_token_usage": int(pt.size),     # rows where the backend reported usage
        "prompt_chars_mean": q(pc, np.mean),
        "prompt_chars_median": q(pc, np.median),
        "prompt_chars_p95": q(pc, lambda a: np.percentile(a, 95)),
        "prompt_chars_max": q(pc, np.max),
        "prompt_tokens_mean": q(pt, np.mean),
        "prompt_tokens_median": q(pt, np.median),
        "prompt_tokens_p95": q(pt, lambda a: np.percentile(a, 95)),
        "prompt_tokens_max": q(pt, np.max),
        "completion_tokens_mean": q(ct, np.mean),
        "completion_tokens_max": q(ct, np.max),
        "total_tokens_sum": (round(float(tt.sum()), 0) if tt.size else None),
        "chars_per_token": (round(float(pc.sum() / pt.sum()), 2)
                            if pt.size and pt.sum() else None),
    }


if enabled:
    assert products and model_keys, "params 'products' and 'model_keys' are required"
    root = Path(products)
    rows = []
    for mk in model_keys:
        path = root / mk / sidecar_name
        if not path.exists():
            logger.info(f"skip {mk}: {sidecar_name} not found")
            continue
        cols = _collect(path)
        row = _stats(mk, cols)
        rows.append(row)
        logger.info(f"{mk}: n={row['n_records']} "
                    f"prompt_tokens(mean/p95/max)="
                    f"{row['prompt_tokens_mean']}/{row['prompt_tokens_p95']}/"
                    f"{row['prompt_tokens_max']} chars/tok={row['chars_per_token']}")

    Path(product["csv"]).parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(product["csv"], index=False)
    logger.info(f"wrote {len(rows)} model rows → {product['csv']}")
else:
    logger.info("usage_stats disabled — skipping")
