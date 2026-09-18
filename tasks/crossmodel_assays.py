"""
crossmodel_assays.py — Ploomber task: cross-model consensus of assay extractions.

The assay analogue of crossmodel_compare.py. Assays have NO ground truth, so cross-model
consensus is the primary correctness proxy: per KE, equivalent assays are clustered ACROSS
models (assay_compare.cluster_ke_assays); a cluster's *support* = number of models that
independently extracted an equivalent assay. Also emits pairwise Jaccard agreement and a
per-model summary collating the GT-free per-model signals (evidence grounding, IRI
validity) when present.

No Ploomber upstream — reads each model's `{products}/{model_key}/assays.json` by path.

Params: products, model_keys, embedding_model, tau, min_support.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from itertools import combinations
from pathlib import Path

from tasks.assay_compare import cluster_ke_assays, DEFAULT_ASSAY_TAU
from tasks.kec_compare import Embedder
from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = None
product = None
enabled = True
products = None
model_keys = None
embedding_model = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
tau = DEFAULT_ASSAY_TAU
min_support = 2
# -

logger = setup_logging(Path(product["summary"]).parent / "crossmodel_assays.log")


# method_type is a controlled vocabulary in the extraction prompt (in_vitro | in_vivo |
# in_silico | ex_vivo | not_specified), but models return it inconsistently punctuated:
# "in vivo", "In-Vivo", "invivo". Those spellings would cluster as different values and
# then reach viz_assay_consensus.json — and from there Solr and the facet figures — as
# separate categories. Canonicalised on load, so the cluster representative written
# downstream carries the vocabulary value while each model's own assays.json keeps
# verbatim what the model produced.
_METHOD_TYPE_VOCAB = ("in_vitro", "in_vivo", "in_silico", "ex_vivo")


def _canonical_method_type(value):
    """Punctuation/case variants of the method_type vocabulary → the vocabulary token.

    A value outside the vocabulary is returned normalised but not remapped: unexpected
    values stay visible rather than being silently bucketed into a known category.
    """
    if not isinstance(value, str) or not value.strip():
        return value
    token = value.strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in token:
        token = token.replace("__", "_")
    for canonical in _METHOD_TYPE_VOCAB:
        if token in (canonical, canonical.replace("_", "")):
            return canonical
    return token


def _load_models(products_root: str, keys: list) -> dict:
    """key -> assays.json dict ({ke: [assay,...]}), skipping any missing.

    method_type is canonicalised here (see _canonical_method_type) so both the
    clustering comparison and the representative that flows into
    viz_assay_consensus.json use the controlled-vocabulary spelling.
    """
    loaded = {}
    for k in keys:
        p = Path(products_root) / k / "assays.json"
        if not p.exists():
            logger.warning(f"skip '{k}': {p} not found")
            continue
        with open(p, encoding="utf-8") as f:
            by_ke = json.load(f)
        n_norm = 0
        for records in by_ke.values():
            for rec in records or []:
                if not isinstance(rec, dict):
                    continue
                before = rec.get("method_type")
                after = _canonical_method_type(before)
                if after != before:
                    rec["method_type"] = after
                    n_norm += 1
        loaded[k] = by_ke
        logger.info(f"loaded {k}: {len(by_ke)} KEs from {p}"
                    + (f" ({n_norm} method_type values normalised)" if n_norm else ""))
    return loaded


def _load_summary(products_root: str, key: str, fname: str) -> dict:
    p = Path(products_root) / key / fname
    if p.exists():
        try:
            doc = json.load(open(p, encoding="utf-8"))
            return doc.get("summary", doc)  # evidence-check writes a flat summary
        except (json.JSONDecodeError, AttributeError):
            return {}
    return {}


if enabled:
    assert products and model_keys, "params 'products' and 'model_keys' are required"
    OUT_CONSENSUS = Path(product["consensus"])
    OUT_PAIRWISE = Path(product["pairwise"])
    OUT_SUMMARY = Path(product["summary"])
    OUT_CONSENSUS.parent.mkdir(parents=True, exist_ok=True)

    models = _load_models(products, list(model_keys))
    present = list(models.keys())
    if len(present) < 2:
        logger.warning(f"only {len(present)} model(s) — pairwise/consensus limited")

    embedder = Embedder(embedding_model)
    all_kes = sorted({kid for m in models.values() for kid in m})

    pair_inter = {frozenset(p): 0 for p in combinations(present, 2)}
    pair_union = {frozenset(p): 0 for p in combinations(present, 2)}

    consensus = {}
    support_hist = {}
    per_model_assaycount = {k: 0 for k in present}
    in_consensus = Counter()      # model -> # of its assays in support≥min_support clusters

    for kid in all_kes:
        m2a = {k: (models[k].get(kid) or []) for k in present}
        for k in present:
            per_model_assaycount[k] += len(m2a[k])
        if not any(m2a.values()):
            continue
        clusters = cluster_ke_assays(m2a, embedder=embedder, tau=tau)

        ke_clusters = []
        for c in clusters:
            sup = sorted(c["support"])
            support_hist[len(sup)] = support_hist.get(len(sup), 0) + 1
            members = [{"model": mk, "assay": m2a[mk][idx], "assay_idx": idx}
                       for (mk, idx) in c["members"]]
            ke_clusters.append({
                "support": len(sup),
                "models": sup,
                "assay": c["representative"],
                "members": members,
            })
            if len(sup) >= min_support:
                for mk in {m["model"] for m in members}:
                    in_consensus[mk] += sum(1 for m in members if m["model"] == mk)
            for a, b in combinations(present, 2):
                key = frozenset((a, b))
                a_in, b_in = a in c["support"], b in c["support"]
                if a_in or b_in:
                    pair_union[key] += 1
                if a_in and b_in:
                    pair_inter[key] += 1
        consensus[kid] = ke_clusters

    # ── per-model summary (GT-free signals) ───────────────────────────────────
    per_model = {}
    for k in present:
        n_assays = per_model_assaycount[k]
        ev = _load_summary(products, k, "assay_evidence_check.json")
        iri = _load_summary(products, k, "validate_assay_iris.json")
        per_model[k] = {
            "n_ke": len(models[k]),
            "n_assays": n_assays,
            "consensus_agreement": (in_consensus[k] / n_assays) if n_assays else None,
            "evidence_grounded_frac": ev.get("evidence_grounded_frac"),
            "no_evidence_frac": ev.get("no_evidence_frac"),
            "iri_valid_frac": iri.get("iri_valid_frac"),
        }

    n_clusters = sum(len(v) for v in consensus.values())
    n_consensus = sum(1 for v in consensus.values()
                      for c in v if c["support"] >= min_support)
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
        "tau": tau, "embedding_model": embedding_model,
    }
    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "consensus_by_ke": consensus}, f,
                  indent=2, ensure_ascii=False)
    with open(OUT_CONSENSUS, "w", encoding="utf-8") as f:
        json.dump(consensus, f, indent=2, ensure_ascii=False)

    with open(OUT_PAIRWISE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["model_a", "model_b", "jaccard", "shared", "union"])
        for a, b in combinations(present, 2):
            key = frozenset((a, b))
            u = pair_union[key]
            jac = (pair_inter[key] / u) if u else None
            w.writerow([a, b, round(jac, 4) if jac is not None else "",
                        pair_inter[key], u])

    logger.info(f"models={present}  KEs(union)={len(all_kes)}  clusters={n_clusters}  "
                f"consensus(≥{min_support})={n_consensus}")
    logger.info(f"support histogram: {summary['support_histogram']}")
    for k, v in per_model.items():
        logger.info(f"  {k:<28} assays={v['n_assays']} "
                    f"consensus_agreement={v['consensus_agreement']} "
                    f"evidence={v['evidence_grounded_frac']}")
else:
    logger.info("crossmodel_assays disabled — skipping")
