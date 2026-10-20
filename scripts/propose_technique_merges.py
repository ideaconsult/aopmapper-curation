"""Propose (for HUMAN review) merges of assay technique names. Nothing is merged here.

Reads the assay_technique / ke_assay docs of aop_solr_assay.json, normalises each
technique name with the consolidation function (tasks.assay_names.punct_normalize),
embeds the unique normalised names with the same SapBERT Embedder the cross-model assay
task uses (tasks.kec_compare.Embedder), and lists every pair with cosine >= --min-sim.

Each pair carries a 'risky' flag: the names differ by a distinguishing token (a token with
a digit, or a short alphanumeric token present on one side only, e.g. RT-PCR vs qPCR,
ELISA vs ELISPOT, HPLC vs UPLC, TG 455 vs TG 487), where a high embedding cosine is known
to be misleading.

Usage:
    uv run python scripts/propose_technique_merges.py [solr_assay_json]
        [--out PATH] [--min-sim 0.80] [--model NAME] [--short-len 6]

Output TSV columns: name_a,name_b,sim,jaccard,risky,n_ke_a,n_ke_b,n_rec_a,n_rec_b
(sorted by sim desc, then name). Deterministic for a fixed model.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tasks.assay_synonym_merge import *  # noqa: E402,F401,F403  (shared logic; CLI below)
from tasks.assay_synonym_merge import (  # noqa: E402
    SHORT_LEN, DEFAULT_SYNONYMS, DEFAULT_APPLIED, apply_candidates, audit_applied,
    default_embed, format_summary, load_techniques, propose, read_candidates, summarise,
    write_tsv)

DEFAULT_INPUT = Path("../aopmapper-curation-data/aopwiki_20260701/curated/solr/"
                     "aop_solr_assay.json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input", nargs="?", default=str(DEFAULT_INPUT))
    ap.add_argument("--out", default=None, help="default: merge_candidates.tsv next to input")
    ap.add_argument("--min-sim", type=float, default=0.80)
    ap.add_argument("--model", default=None,
                    help="default: tasks.kec_compare.DEFAULT_EMBEDDING_MODEL (SapBERT, "
                         "= embedding_model of env.assays_consensus.yaml)")
    ap.add_argument("--short-len", type=int, default=SHORT_LEN)
    ap.add_argument("--apply", action="store_true",
                    help="fold accepted pairs into the synonym file (see plan_merges)")
    ap.add_argument("--apply-threshold", type=float, default=0.90)
    ap.add_argument("--dry-run", action="store_true",
                    help="with --apply: print groups and counts, write nothing")
    ap.add_argument("--audit", default=None, metavar="APPLIED_TSV",
                    help="read-only: list applied merges with digit/numeral/Greek/antonym "
                         "differences, then exit")
    ap.add_argument("--synonyms", default=str(DEFAULT_SYNONYMS))
    ap.add_argument("--applied-log", default=str(DEFAULT_APPLIED))
    ap.add_argument("--candidates", default=None,
                    help="with --apply: read this merge_candidates.tsv instead of "
                         "re-embedding")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")      # names carry greek letters etc.

    if args.audit:
        flagged = audit_applied(Path(args.audit))
        print(f"{len(flagged)} applied merges flagged")
        for x in flagged:
            print(f"  [{x['why']}] {x['variant']} -> {x['canonical']} ({x['sim']})")
        return 0
    if args.apply and args.candidates:
        print(apply_candidates(read_candidates(Path(args.candidates)),
                               Path(args.synonyms), Path(args.applied_log),
                               args.apply_threshold, args.dry_run))
        return 0

    inp = Path(args.input)
    out = Path(args.out) if args.out else inp.parent / "merge_candidates.tsv"
    if args.model is None:
        from tasks.kec_compare import DEFAULT_EMBEDDING_MODEL as model
    else:
        model = args.model

    techs = load_techniques(json.loads(inp.read_text(encoding="utf-8")))
    rows = propose(techs, default_embed(model), args.min_sim, args.short_len)
    write_tsv(rows, out)
    print(f"model: {model}\ninput: {inp}\nwrote {len(rows)} pairs (sim >= {args.min_sim}) "
          f"to {out}")
    print(format_summary(techs, summarise(techs, rows, args.min_sim)))
    if args.apply:
        print(apply_candidates(rows, Path(args.synonyms), Path(args.applied_log),
                               args.apply_threshold, args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
