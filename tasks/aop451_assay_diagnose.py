"""
aop451_assay_diagnose.py - why does the cluster-scored panel figure fall below a single model?

aop451_reference_check scores the pooled panel by matching each recommended method against
the *representative* assay of every cross-model cluster (viz_assay_consensus.json), while a
per-model result matches against the model's own assays. A model whose matching assay is
not its cluster's representative is lost by the first scoring. This tool lists, for one
model, every recommended method it matches on its own but the cluster scoring misses, and
for each shows the model's matching assay, the cluster it was merged into, that cluster's
representative, and the assay_match verdict of the method against both.

Import-safe library plus a CLI (no Ploomber body):

  uv run python -m tasks.aop451_assay_diagnose \\
      --reference-check ../aopmapper-curation-data/aopwiki_20260701/KEC_ALL/aop451_reference_check.json \\
      --viz ../aopmapper-curation-data/aopwiki_20260701/ASSAYS/crossmodel/manuscript/viz_assay_consensus.json \\
      --reference resources/aop451_reference.json \\
      --model poolside--Laguna-S-2.1-NVFP4 [--no-embedder]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_json(path) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        return json.load(f)


def _verdict(ref: dict, rec: dict, embedder, tau: float) -> dict:
    from tasks.assay_compare import assay_match
    matched, score, method = assay_match(ref, rec, embedder=embedder, tau=tau)
    return {"matched": matched, "score": round(score, 4), "method": method}


def _slim(rec: dict) -> dict:
    return {k: rec.get(k) for k in ("assay_name", "measured_endpoint", "method_type")}


def divergent_methods(results: dict, consensus_assays: dict, model: str) -> list[int]:
    """Indices of the recommended methods that `model` matches on its own and the
    cluster-scored panel does not. Asserts that the two score the same methods in the same
    order (index equality is how the union is taken)."""
    own = results[model]["assays"]["assays"]
    cons = consensus_assays["assays"]
    assert [r["reference_assay"] for r in own] == [r["reference_assay"] for r in cons], \
        "per-model and consensus rows list different methods / orders"
    return [i for i, (a, c) in enumerate(zip(own, cons)) if a["recovered"] and not c["recovered"]]


def diagnose_method(i: int, model: str, results: dict, consensus_assays: dict, viz: dict,
                    reference: dict, embedder, tau: float) -> dict:
    """Evidence for one divergent method index i."""
    own = results[model]["assays"]["assays"][i]
    ref = reference["standardized_assays"][i]
    assert ref["assay_name"] == own["reference_assay"], "reference fixture order mismatch"
    best = own["best_match"]
    ke_id = best["ke_id"]
    ke = next((k for k in viz.get("kes", []) if k.get("ke_id") == ke_id), None)
    out = {"index": i, "reference_assay": own["reference_assay"],
           "relevant_ke_groups": own["relevant_ke_groups"],
           "model_best_match": best,
           "consensus_best_match": consensus_assays["assays"][i]["best_match"],
           "ke_in_viz": ke is not None, "clusters": []}
    for c in (ke or {}).get("clusters") or []:
        mine = [m for m in c.get("members") or []
                if m.get("model") == model
                and (m.get("assay") or {}).get("assay_name") == best["assay_name"]]
        if not mine:
            continue
        rep = c.get("assay") or {}
        out["clusters"].append({
            "support": c.get("support"),
            "n_members": len(c.get("members") or []),
            "representative": _slim(rep),
            "member_names": sorted({(m.get("assay") or {}).get("assay_name")
                                    for m in c.get("members") or []}),
            "model_member": _slim(mine[0].get("assay") or {}),
            "ref_vs_model_member": _verdict(ref, mine[0].get("assay") or {}, embedder, tau),
            "ref_vs_representative": _verdict(ref, rep, embedder, tau),
        })
    return out


def diagnose(results: dict, viz: dict, reference: dict, consensus_assays: dict,
             model: str, embedder, tau: float) -> dict:
    own = results[model]["assays"]
    return {
        "model": model, "tau": tau,
        "model_n_recovered": own["n_recovered"],
        "consensus_n_recovered": consensus_assays["n_recovered"],
        "divergent": [diagnose_method(i, model, results, consensus_assays, viz, reference,
                                      embedder, tau)
                      for i in divergent_methods(results, consensus_assays, model)],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reference-check", required=True, help="aop451_reference_check.json")
    ap.add_argument("--viz", required=True, help="viz_assay_consensus.json")
    ap.add_argument("--reference", default="resources/aop451_reference.json")
    ap.add_argument("--model", required=True, help="model key, e.g. poolside--Laguna-S-2.1-NVFP4")
    ap.add_argument("--tau", type=float, default=0.70)
    ap.add_argument("--embedding-model", default="cambridgeltl/SapBERT-from-PubMedBERT-fulltext")
    ap.add_argument("--no-embedder", action="store_true",
                    help="exact/name-only matching (as the reference check falls back to)")
    args = ap.parse_args(argv)

    check = _load_json(args.reference_check)
    embedder = None
    if not args.no_embedder:
        from tasks.kec_compare import Embedder
        embedder = Embedder(args.embedding_model)
    report = diagnose(check["results"], _load_json(args.viz), _load_json(args.reference),
                      check["consensus_assays"], args.model, embedder, args.tau)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
