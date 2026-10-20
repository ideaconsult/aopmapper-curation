"""Recompute the quantitative claims of results_4b_assays.md from raw pipeline products.

Read-only. Reads the 2026-07-01 ASSAYS / KEC_ALL crossmodel products and prints a JSON
dict of recomputed values, so the manuscript text can be compared against products rather
than against the (possibly stale) exported stats.

Usage:
    uv run python scripts/verify_assay_results.py [data_root] [--ke KE350]
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

DEFAULT_ROOT = Path("../aopmapper-curation-data/aopwiki_20260701")
ASSAY_DIR = "ASSAYS/crossmodel/manuscript"
KEC_DIR = "KEC_ALL/crossmodel/manuscript"


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _tsv(p: Path, delim="\t"):
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=delim))


def ke_support_counts(consensus: dict, n_ke_union: int, min_support: int = 2) -> dict:
    """KEs with a cluster at >= min_support / only below it / no cluster at all."""
    at = below = 0
    for clusters in consensus.values():
        if any(c["support"] >= min_support for c in clusters):
            at += 1
        elif clusters:
            below += 1
    return {"ke_at_threshold": at, "ke_only_below": below,
            "ke_without_record": n_ke_union - at - below}


def cross_track_totals(rows: list[dict]) -> dict:
    scored = sum(int(r["n_assay_triples_scored"]) for r in rows)
    m = sum(int(r["n_matched"]) for r in rows)
    ms = sum(int(r["n_matched_strict"]) for r in rows)
    lo = [float(r["cross_track_consistency"]) for r in rows]
    los = [float(r["cross_track_consistency_strict"]) for r in rows]
    return {"scored": scored, "matched": m, "matched_strict": ms,
            "pooled": m / scored, "pooled_strict": ms / scored,
            "range": [min(lo), max(lo)], "range_strict": [min(los), max(los)],
            "max_n_ke_scored": max(int(r["n_ke_scored"]) for r in rows)}


def ke_example(consensus: dict, ke: str) -> dict:
    out = []
    for c in consensus.get(ke, []):
        a = c["assay"]
        out.append({"support": c["support"], "assay_name": a.get("assay_name"),
                    "method_type": a.get("method_type"), "species": a.get("species"),
                    "endpoint": a.get("measured_endpoint"),
                    "direction": a.get("endpoint_direction"), "guideline": a.get("guideline"),
                    "ontology_terms": a.get("ontology_terms"), "evidence": a.get("evidence")})
    return {"n_clusters": len(out), "clusters": out}


def collect(root: Path, ke: str = "KE350") -> dict:
    a = root / ASSAY_DIR
    summ = _json(a / "crossmodel_summary.json")["summary"]
    cons = _json(a / "crossmodel_consensus.json")
    hist = summ["support_histogram"]
    n_cl, n_con = summ["n_clusters"], summ["n_consensus_clusters"]
    kec = _json(root / KEC_DIR / "crossmodel_summary.json")["summary"]
    ms = _tsv(a / "assay_model_summary_all.csv", ",")
    smaa = _tsv(a / "default/smaa_rank.tsv")
    kec_smaa = _tsv(root / KEC_DIR / "default/smaa_rank.tsv")
    ev = {r["model_key"]: float(r["evidence_grounded_frac"]) for r in ms}
    iri = {r["model_key"]: float(r["iri_valid_frac"]) for r in ms}
    tech = next(root.glob("ASSAYS/assays/solr/assay_technique_stats.json"), None)
    return {
        "scale": {"n_ke_union": summ["n_ke_union"], "n_models": len(summ["models_present"]),
                  "n_clusters": n_cl, "n_consensus": n_con, "n_single": hist["1"],
                  "pct_consensus": n_con / n_cl, "pct_single": hist["1"] / n_cl,
                  "sum_check": n_con + hist["1"], "tau_in_summary": summ.get("tau"),
                  **ke_support_counts(cons, summ["n_ke_union"])},
        "kec_all": {"n_clusters": kec["n_clusters"], "n_consensus": kec["n_consensus_clusters"],
                    "pct_consensus": kec["n_consensus_clusters"] / kec["n_clusters"]},
        "technique_stats_file": str(tech) if tech else None,
        "cross_track": cross_track_totals(_tsv(a / "crossmodel_track_consistency.tsv")),
        "evidence_grounded": {"min": min(ev.values()), "max": max(ev.values()), "per_model": ev},
        "iri_valid": iri,
        "smaa_assay": [(r["model"], float(r["first_rank_prob"])) for r in smaa[:3]],
        "smaa_kec_all": [(r["model"], float(r["first_rank_prob"])) for r in kec_smaa[:3]],
        ke: ke_example(cons, ke),
    }


def main(argv: list[str]) -> int:
    args = [x for x in argv if not x.startswith("--")]
    root = Path(args[0]) if args else DEFAULT_ROOT
    ke = argv[argv.index("--ke") + 1] if "--ke" in argv else "KE350"
    if ke in args:
        args.remove(ke)
        root = Path(args[0]) if args else DEFAULT_ROOT
    print(json.dumps(collect(root, ke), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
