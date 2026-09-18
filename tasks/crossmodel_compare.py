"""
crossmodel_compare.py — Ploomber task: cross-model comparison of KEC extractions.

Generic over a configured list of model keys and a product folder. For each model it
reads `{products}/{model_key}/kec.json` (produced by separate per-model runs of
`pipeline.kec.yaml`) and produces:

  1. **Consensus** — per KE, equivalent LLM triples are clustered ACROSS models via the
     hybrid matcher (tasks/kec_compare.cluster_ke_triples). A cluster's *support* = number
     of models that independently produced an equivalent triple. Consensus is a
     ground-truth-free trust signal (spec §7.4).
  2. **Pairwise agreement** — model × model Jaccard over clustered triples.
  3. **Per-model summary** — collates each model's existing per-model metric files
     (match report, log-prob calibration, ground-truth audit) when present, turning the
     single-model pilot numbers into a cross-model table.

This is a SEPARATE pipeline (`pipeline.consensus.yaml`) because models are run one at a
time (config-swapped) on the local accelerator; this task is run once afterwards over all
of their product directories. It has no Ploomber upstream — it reads the per-model
`kec.json` files by path.

Params: products (root folder), model_keys (list), embedding_model, tau.
"""
from __future__ import annotations

import csv
import json
from itertools import combinations
from pathlib import Path

from tasks.kec_compare import (
    Embedder, cluster_ke_triples, llm_triples, iter_annotated, match_ke,
)
from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = None            # no DAG upstream — reads per-model kec.json by path
product = None
enabled = True
products = None            # product root folder, e.g. "products_202606"
model_keys = None          # list of model-key subfolder names to compare
embedding_model = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
tau = 0.75
min_support = 2            # consensus threshold (≥ this many models)
include_ground_truth = True   # add curated `structured` triples as a pairwise pseudo-model
gt_key = "ground_truth"       # its row/column label in the pairwise matrix
# -

logger = setup_logging(Path(product["summary"]).parent / "crossmodel_compare.log")


def _load_models(products_root: str, keys: list) -> dict:
    """key -> kec.json dict, skipping any that are missing."""
    loaded = {}
    for k in keys:
        p = Path(products_root) / k / "kec.json"
        if not p.exists():
            logger.warning(f"skip '{k}': {p} not found")
            continue
        with open(p, encoding="utf-8") as f:
            loaded[k] = json.load(f)
        logger.info(f"loaded {k}: {len(loaded[k])} KEs from {p}")
    return loaded


def _load_summary(products_root: str, key: str, fname: str) -> dict:
    p = Path(products_root) / key / fname
    if p.exists():
        try:
            return json.load(open(p, encoding="utf-8")).get("summary", {})
        except (json.JSONDecodeError, AttributeError):
            return {}
    return {}


def _load_json(products_root: str, key: str, fname: str) -> dict:
    """Like _load_summary, but for files with no top-level "summary" envelope
    (e.g. kec_logprob_calibration.json, whose best_auc/best_feature are top-level)."""
    p = Path(products_root) / key / fname
    if p.exists():
        try:
            return json.load(open(p, encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


if enabled:
    assert products and model_keys, "params 'products' and 'model_keys' are required"
    OUT_CONSENSUS = Path(product["consensus"])
    OUT_PAIRWISE  = Path(product["pairwise"])
    OUT_SUMMARY   = Path(product["summary"])
    OUT_CONSENSUS.parent.mkdir(parents=True, exist_ok=True)

    models = _load_models(products, list(model_keys))
    present = list(models.keys())
    if len(present) < 2:
        logger.warning(f"only {len(present)} model(s) available — pairwise/consensus limited")

    embedder = Embedder(embedding_model)

    def _gt_for_ke(kid: str) -> list:
        """Curated structured triples for a KE (same across models — take first present)."""
        for k in present:
            st = (models[k].get(kid) or {}).get("structured") or []
            if st:
                return st
        return []

    # union of all KE ids across models
    all_kes = sorted({kid for m in models.values() for kid in m})

    # Ground truth is added as a pairwise pseudo-model so the heatmap shows, for each
    # model, its Jaccard agreement with the curated triples (a recall-like row). GT is
    # NOT counted in consensus support, the support histogram, or the per-model summary.
    nodes = list(present) + ([gt_key] if include_ground_truth else [])

    # pairwise accumulators (Jaccard over clusters where each member appears)
    pair_inter = {frozenset(p): 0 for p in combinations(nodes, 2)}
    pair_union = {frozenset(p): 0 for p in combinations(nodes, 2)}

    consensus = {}              # ke_id -> [cluster summaries]
    support_hist = {}           # support count -> n clusters
    per_model_triplecount = {k: 0 for k in present}

    for kid in all_kes:
        m2t = {k: llm_triples(models[k].get(kid, {})) for k in present}
        for k in present:
            per_model_triplecount[k] += len(m2t[k])
        gt = _gt_for_ke(kid) if include_ground_truth else []
        cluster_input = dict(m2t)
        if gt:
            cluster_input[gt_key] = gt
        if not any(cluster_input.values()):
            continue
        clusters = cluster_ke_triples(cluster_input, embedder=embedder, tau=tau)
        # GT participates in pairwise for this KE only if it actually has triples here
        ke_nodes = list(present) + ([gt_key] if gt else [])

        ke_clusters = []
        for c in clusters:
            # pairwise agreement over every node (incl. GT) — done for ALL clusters
            for a, b in combinations(ke_nodes, 2):
                key = frozenset((a, b))
                a_in, b_in = a in c["support"], b in c["support"]
                if a_in or b_in:
                    pair_union[key] += 1
                if a_in and b_in:
                    pair_inter[key] += 1
            # consensus / support are over REAL models only; a GT-only cluster
            # (a curated triple no model reproduced) is not a model-consensus cluster.
            real_support = sorted(m for m in c["support"] if m != gt_key)
            if not real_support:
                continue
            support_hist[len(real_support)] = support_hist.get(len(real_support), 0) + 1
            # Preserve the FULL nested triple structure (kec.json-compatible:
            # biological_object/process/action, each {name, iri, source, logprob...}).
            # `triple` is the cluster representative; `members` keeps each model's
            # own triple so per-model IRI/source/logprob differences are not lost.
            ke_clusters.append({
                "support": len(real_support),
                "models": real_support,
                "in_ground_truth": gt_key in c["support"],   # matches a curated triple
                "triple": c["representative"],
                "members": [{"model": mk, "triple": cluster_input[mk][idx],
                             "triple_idx": idx}
                            for (mk, idx) in c["members"] if mk != gt_key],
            })
        consensus[kid] = ke_clusters

    # ── per-model summary: collate existing per-model metric files ────────────
    per_model = {}
    for k in present:
        match_s = _load_summary(products, k, "kec_match_report.json")
        calib_s = _load_json(products, k, "kec_logprob_calibration.json")
        audit_s = _load_summary(products, k, "kec_truth_audit.json")
        # match rate fallback computed from kec.json if the report is absent
        match_rate = match_s.get("match_rate")
        if match_rate is None:
            n_pred = n_match = 0
            for _kid, structured, llm in iter_annotated(models[k]):
                for mm in match_ke(llm, structured, embedder=embedder, tau=tau):
                    n_pred += 1
                    n_match += int(mm["matched"])
            match_rate = (n_match / n_pred) if n_pred else None
        per_model[k] = {
            "n_ke": len(models[k]),
            "n_llm_triples": per_model_triplecount[k],
            "match_rate": match_rate,
            "logprob_best_feature": calib_s.get("best_feature"),
            "logprob_best_auc": calib_s.get("best_auc"),
            "audit_verdict_fraction": audit_s.get("triple_verdict_fraction")
                                      or audit_s.get("verdict_fraction"),
        }

    # ── write outputs ─────────────────────────────────────────────────────────
    n_clusters = sum(len(v) for v in consensus.values())
    n_consensus = sum(1 for v in consensus.values() for c in v if c["support"] >= min_support)
    summary = {
        "products": products,
        "models_requested": list(model_keys),
        "models_present": present,
        "n_ke_union": len(all_kes),
        "n_clusters": n_clusters,
        "n_consensus_clusters": n_consensus,
        "min_support": min_support,
        "support_histogram": dict(sorted(support_hist.items())),
        "per_model": per_model,
        "ground_truth_key": gt_key if include_ground_truth else None,
        "pairwise_nodes": nodes,
        "tau": tau, "embedding_model": embedding_model,
    }
    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "consensus_by_ke": consensus}, f,
                  indent=2, ensure_ascii=False)
    # consensus also written standalone for convenience
    with open(OUT_CONSENSUS, "w", encoding="utf-8") as f:
        json.dump(consensus, f, indent=2, ensure_ascii=False)

    # pairwise Jaccard matrix
    with open(OUT_PAIRWISE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["model_a", "model_b", "jaccard", "shared", "union"])
        for a, b in combinations(nodes, 2):
            key = frozenset((a, b))
            u = pair_union[key]
            jac = (pair_inter[key] / u) if u else None
            w.writerow([a, b, round(jac, 4) if jac is not None else "", pair_inter[key], u])

    logger.info(f"models={present}  KEs(union)={len(all_kes)}  clusters={n_clusters}  "
                f"consensus(≥{min_support})={n_consensus}")
    logger.info(f"support histogram: {summary['support_histogram']}")
    for k, v in per_model.items():
        logger.info(f"  {k:<12} triples={v['n_llm_triples']} match_rate={v['match_rate']} "
                    f"logprob_AUC={v['logprob_best_auc']}")
else:
    logger.info("crossmodel_compare disabled — skipping")
