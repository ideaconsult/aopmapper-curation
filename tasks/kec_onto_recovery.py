"""
kec_onto_recovery.py — Ploomber task: per-ontology recovery of curated KEC triples.

Answers a question crossmodel_compare's cluster-level `in_ground_truth` flag cannot:
of the curated triples themselves (not clusters — several curated triples for one Key
Event can merge into a single cluster under the hybrid matcher), what fraction is
recovered by the panel, broken down by the source ontology of the triple's process slot
(and, separately, its object slot)?

For each Key Event, the curated `structured` triples are read directly from any one
model's kec.json (ground truth is schema-identical across models — same source as
crossmodel_compare._gt_for_ke). Each curated triple is matched, via the same hybrid
matcher used everywhere else in this codebase (tasks.kec_compare.triple_match), against
that KE's own consensus clusters — not against raw per-model predictions, since a
cluster's representative is already the pooled, deduplicated form of whatever the panel
proposed. A curated triple counts as recovered if it matches ANY cluster with support
>= min_support (the same consensus threshold used throughout).

This is therefore a true per-triple count, consistent with and complementary to
crossmodel_compare's per-cluster in_ground_truth flag: the two will not in general agree
in magnitude (clusters can under- or over-count triples), and that is expected, not a bug.

Ploomber:
  upstream: none (reads finished kec.json + crossmodel_consensus.json products directly)
  product:  {"json": per-ontology recovery stats}
  params:   products_dir, model_keys, crossmodel_dir, min_support, embedding_model, tau
"""
from __future__ import annotations

import json
from pathlib import Path

from tasks.kec_compare import Embedder, DEFAULT_TAU, DEFAULT_EMBEDDING_MODEL, triple_match
from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = None
product = None
enabled = True
products_dir = None        # root holding per-model kec.json dirs
model_keys = None           # the selected panel's model-key subfolder names
crossmodel_dir = None        # dir holding crossmodel_consensus.json for the same panel/release
min_support = 2               # consensus threshold, same as crossmodel_compare's min_support
embedding_model = DEFAULT_EMBEDDING_MODEL
tau = DEFAULT_TAU
# -

logger = setup_logging(Path(product["json"]).parent / "kec_onto_recovery.log")


def _gt_for_ke(models: dict, kid: str) -> list:
    """Curated structured triples for a KE — same across models, take first present."""
    for m in models.values():
        st = (m.get(kid) or {}).get("structured") or []
        if st:
            return st
    return []


def _slot_source(triple: dict, slot: str) -> str | None:
    """slot is 'object' or 'process' — the curated (structured) schema's own key names."""
    term = triple.get(slot)
    return (term or {}).get("source") or None


def _two_proportion_z(x1: int, n1: int, x2: int, n2: int) -> float | None:
    """z for the difference of two proportions (pooled variance), two-sided."""
    if n1 == 0 or n2 == 0:
        return None
    p1, p2 = x1 / n1, x2 / n2
    p_pool = (x1 + x2) / (n1 + n2)
    denom = (p_pool * (1 - p_pool) * (1 / n1 + 1 / n2)) ** 0.5
    if denom == 0:
        return None
    return (p1 - p2) / denom


if enabled:
    assert products_dir and model_keys, "params 'products_dir' and 'model_keys' are required"
    assert crossmodel_dir, "param 'crossmodel_dir' is required"

    models = {}
    for k in model_keys:
        p = Path(products_dir) / k / "kec.json"
        if not p.exists():
            logger.warning(f"skip '{k}': {p} not found")
            continue
        with open(p, encoding="utf-8") as f:
            models[k] = json.load(f)
    assert models, f"no model kec.json files found under {products_dir}"

    consensus_path = Path(crossmodel_dir) / "crossmodel_consensus.json"
    with open(consensus_path, encoding="utf-8") as f:
        consensus_by_ke: dict = json.load(f)

    embedder = Embedder(embedding_model)

    # slot -> source -> {"total": n, "recovered": n}
    by_slot_source: dict[str, dict[str, dict[str, int]]] = {"process": {}, "object": {}}

    all_kes = sorted({kid for m in models.values() for kid in m})
    for kid in all_kes:
        gt_triples = _gt_for_ke(models, kid)
        if not gt_triples:
            continue
        clusters = [c for c in consensus_by_ke.get(kid, []) if c.get("support", 0) >= min_support]
        for t in gt_triples:
            recovered = any(
                triple_match(t, c["triple"], embedder=embedder, tau=tau)[0]
                for c in clusters
            )
            for slot in ("process", "object"):
                src = _slot_source(t, slot)
                if not src:
                    continue
                bucket = by_slot_source[slot].setdefault(src, {"total": 0, "recovered": 0})
                bucket["total"] += 1
                bucket["recovered"] += int(recovered)

    def _rate(b: dict) -> float | None:
        return (b["recovered"] / b["total"]) if b["total"] else None

    out = {
        "min_support": min_support,
        "by_slot_source": {
            slot: {
                src: {**b, "rate": _rate(b)}
                for src, b in sorted(sources.items(), key=lambda kv: -kv[1]["total"])
            }
            for slot, sources in by_slot_source.items()
        },
    }

    # Process-slot phenotype/trait pooling (MP, HP, VT) vs GO, the comparison results.md
    # reports — ontology prefixes are fixed by the curated schema, not configurable here.
    proc = by_slot_source["process"]
    go = proc.get("GO", {"total": 0, "recovered": 0})
    pheno_total = sum(proc.get(o, {"total": 0})["total"] for o in ("MP", "HP", "VT"))
    pheno_recovered = sum(proc.get(o, {"recovered": 0})["recovered"] for o in ("MP", "HP", "VT"))
    out["process_go_vs_phenotype"] = {
        "go_total": go["total"], "go_recovered": go["recovered"], "go_rate": _rate(go),
        "phenotype_total": pheno_total, "phenotype_recovered": pheno_recovered,
        "phenotype_rate": (pheno_recovered / pheno_total) if pheno_total else None,
        "z": _two_proportion_z(go["recovered"], go["total"], pheno_recovered, pheno_total),
    }

    with open(Path(product["json"]), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    logger.info(f"wrote {product['json']}")
