"""
export_manuscript_results.py — Ploomber task: pull crossmodel/SMAA results into the
manuscript repo so results.md prose can cite stable, regenerable figures and numbers.

Copies a configured list of figures out of a crossmodel product directory into the
manuscript repo's figures/ folder, and writes a JSON stats file (crossmodel overview +
per-SMAA-variant top-N ranking with criteria values, plus optional worked-example KE
detail) for prose to reference. Re-run any time the underlying products change (e.g. after
a data-quality fix or a panel refresh) — figures and numbers stay in sync without
hand-editing the manuscript.

Worked examples (`worked_example_kes`) are configured once (e.g. the AOP 451 KEs checkable
against a published manual curation) and can be reused verbatim across tracks — KEC and
assay — by pointing `worked_example_models` at the relevant per-model product directories
for each track's own `products` root. When `track_consistency_json` (D5, response_plan.md
R1 §5.3 — the output of tasks/crossmodel_track_consistency.py) is also supplied, each
worked-example (KE, model) pair additionally gets its per-assay cross-track match detail —
read straight from that already-computed product, not recomputed here.

Ploomber:
  upstream: none (reads finished crossmodel/SMAA products directly from disk)
  product:  {"json": stats summary}
  params:   crossmodel_dir, manuscript_repo, section_name, smaa_variants, figures,
            n_top_models, products_dir, assays_products_dir, worked_example_kes,
            worked_example_models, track_consistency_json
"""
from __future__ import annotations

import csv
import json
import shutil
import statistics
from pathlib import Path

from tasks.logutil import setup_logging
from tasks.manuscript_publish import publish_technique_stats

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
product = None
enabled = True
crossmodel_dir = None      # e.g. {{products}}/crossmodel_08  (KEC) or crossmodel_local (assays)
manuscript_repo = None     # root of the manuscript-repo checkout
section_name = None        # e.g. "kec116", "assays" — subfolder under figures/ and stats key
smaa_variants = ["default"]  # subdirs of crossmodel_dir holding smaa_summary.json
figures = []                # list of {"src": "relative/path.png", "out": "name.png"}
n_top_models = 8            # how many ranked models to record per SMAA variant
products_dir = None          # root holding per-model kec.json dirs, for worked examples
assays_products_dir = None   # root holding per-model assays.json dirs (separate track/release)
worked_example_kes = []      # e.g. ["KE870", "KE1496", "KE1497"] — reused across tracks
worked_example_models = []   # model_keys to pull per-KE detail from, e.g. the two SMAA leaders
track_consistency_json = None  # optional: crossmodel_track_consistency.json (D5) — adds
                                # per-assay cross-track match detail to each worked example
technique_stats_json = None    # optional: publish stage's stats product (not in this repo) — distinct
                                # techniques vs per-KE assay records, and reuse examples
reference_check_json = None    # optional: aop451_reference_check.py's json — recall against
                                # the published manual curation (the one outside reference)
viz_assay_consensus_json = None  # optional: assay consensus clusters, for the panel-level
                                  # top-support assays per worked-example KE
lineage_pairs = []             # optional: [{"a": model, "b": model, "relation": str}] -
                                # same-checkpoint pairs whose triple-set Jaccard is reported
                                # against the panel-wide pairwise distribution
onto_recovery_json = None      # optional: tasks/kec_onto_recovery.py's output JSON - per-
                                # ontology curated-triple recovery (true triple granularity,
                                # not the cluster-level crossmodel_overview numbers)
# -

logger = setup_logging(Path(product["json"]).parent / "export_manuscript_results.log")


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        return json.load(f)


def _copy_figures(crossmodel: Path, figures_out: Path) -> list[str]:
    figures_out.mkdir(parents=True, exist_ok=True)
    copied = []
    for spec in figures:
        src = crossmodel / spec["src"]
        if not src.exists():
            logger.warning(f"missing figure, skipped: {src}")
            continue
        dst = figures_out / spec["out"]
        shutil.copy2(src, dst)
        copied.append(spec["out"])
        logger.info(f"{spec['src']} -> {dst}")
    return copied


def _smaa_variant_stats(crossmodel: Path, variant: str) -> dict | None:
    path = crossmodel / variant / "smaa_summary.json"
    if not path.exists():
        logger.warning(f"missing SMAA variant '{variant}': {path}")
        return None
    summary = _load_json(path)
    ranking = summary.get("ranking", [])[:n_top_models]
    return {
        "n_models": summary.get("n_models"),
        "n_iter": summary.get("n_iter"),
        "criteria": summary.get("criteria"),
        "imputed": summary.get("imputed"),
        "total_structured_gt": summary.get("total_structured_gt"),
        "top_models": [
            {
                "model": row["model"],
                "first_rank_prob": row["first_rank_prob"],
                "criteria_values": row["criteria_values"],
            }
            for row in ranking
        ],
    }


#: Ensemble modes read per SMAA variant. The greedy_* names are greedy runs with an explicit
#: smaa_link (see select_ensemble.GREEDY_VARIANTS), each in its own product directory.
ENSEMBLE_MODES = ("greedy", "greedy_none", "greedy_tiebreak", "greedy_prefilter",
                  "ilp", "pareto_smaa", "pareto_topsis")

#: smaa_criteria.tsv column -> direction; must match select_ensemble.QUALITY_DIRECTION.
QUALITY_COLUMNS = {"topsis_score": "higher", "expected_rank": "lower"}


def _panel_quality(crossmodel: Path, variant: str, column: str) -> dict:
    """{model: quality in [0,1]} scaled exactly as select_ensemble._load_quality does -
    min-max over the whole panel in smaa_criteria.tsv, inverted for lower-is-better - so a
    mean over any selected set is on the same scale as the pareto modes' mean_quality."""
    p = crossmodel / variant / "smaa_criteria.tsv"
    if not p.exists():
        return {}
    raw = {}
    with open(p, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            try:
                raw[row["model"]] = float(row[column])
            except (KeyError, TypeError, ValueError):
                continue
    if not raw:
        return {}
    if QUALITY_COLUMNS[column] == "lower":
        raw = {m: -v for m, v in raw.items()}
    lo, hi = min(raw.values()), max(raw.values())
    if hi <= lo:
        return {m: 1.0 for m in raw}
    return {m: (v - lo) / (hi - lo) for m, v in raw.items()}


#: crossmodel_compare scores the curated layer as a pseudo-model under this key; it is not a
#: panel member, so its pairs are left out of the panel-wide distribution.
GROUND_TRUTH_KEY = "ground_truth"


def _lineage_jaccard(crossmodel: Path) -> dict | None:
    """Triple-set Jaccard for each configured same-checkpoint pair (`lineage_pairs`),
    set against the distribution over all panel pairs, from crossmodel_pairwise.tsv."""
    p = crossmodel / "crossmodel_pairwise.tsv"
    if not lineage_pairs or not p.exists():
        return None
    by_pair, values = {}, []
    with open(p, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            a, b = row["model_a"], row["model_b"]
            if GROUND_TRUTH_KEY in (a, b):
                continue
            rec = {"jaccard": float(row["jaccard"]), "shared": int(row["shared"]),
                   "union": int(row["union"])}
            by_pair[frozenset((a, b))] = rec
            values.append(rec["jaccard"])
    pairs = []
    for spec in lineage_pairs:
        rec = by_pair.get(frozenset((spec["a"], spec["b"])))
        if rec is None:
            logger.warning(f"lineage pair not in {p.name}: {spec['a']} / {spec['b']}")
            continue
        pairs.append({**spec, **rec})
    pairs.sort(key=lambda r: -r["jaccard"])
    values.sort()
    return {
        "pairs": pairs,
        "distribution": {
            "n_pairs": len(values),
            "median": round(statistics.median(values), 4),
            "p90": round(statistics.quantiles(values, n=10, method="inclusive")[-1], 4),
            "max": round(values[-1], 4),
        },
    }


#: smaa_criteria.tsv columns -> supplementary panel table headers, in table order.
PANEL_COLUMNS = [
    ("match_rate", "Curated agreement"), ("gt_coverage", "Curated recall"),
    ("method_iri_frac", "Exact-IRI grounding"), ("action_margin_auc", "Action margin"),
    ("consensus_agreement", "Panel agreement"), ("first_rank_prob", "P(rank 1)"),
    ("expected_rank", "Expected rank"), ("topsis_score", "TOPSIS"),
]


def _hf_id(model_key: str) -> str:
    """Model keys are Hugging Face ids with "/" written as "--" (org--name)."""
    return model_key.replace("--", "/", 1)


def _write_panel_table(crossmodel: Path, variant: str, selected: list[str],
                       md_path: Path) -> int:
    """Supplementary table of the whole panel: every model SMAA ranked, its criteria, and
    whether the adopted ensemble selected it. Writes Markdown (for the build) and a TSV of
    the same rows beside it. Returns the number of models written."""
    p = crossmodel / variant / "smaa_criteria.tsv"
    if not p.exists():
        logger.warning(f"panel table skipped, {p} not found")
        return 0
    with open(p, encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    rows.sort(key=lambda r: float(r["expected_rank"]))
    chosen = set(selected)
    header = ["#", "Model"] + [h for _, h in PANEL_COLUMNS] + ["Selected"]
    table = []
    for i, r in enumerate(rows, 1):
        vals = []
        for col, _ in PANEL_COLUMNS:
            v = r.get(col, "")
            vals.append(f"{float(v):.2f}" if col == "expected_rank" and v
                        else (f"{float(v):.3f}" if v else ""))
        table.append([str(i), _hf_id(r["model"])] + vals
                     + ["yes" if r["model"] in chosen else ""])

    md_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---:", "---"] + ["---:"] * len(PANEL_COLUMNS) + [":---:"]) + "|",
    ] + ["| " + " | ".join(row) + " |" for row in table]
    caption = (f"Table: Supplementary Table S1. The {len(rows)}-model pilot panel, ordered by "
               "SMAA expected rank under the five-criterion ranking. Model is the Hugging Face "
               "identifier. Criteria are as defined in Methods; Selected marks the "
               f"{len(chosen)} models of the adopted ensemble.")
    md_path.write_text("\n".join(lines) + "\n\n" + caption + "\n", encoding="utf-8")
    with open(md_path.with_suffix(".tsv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(header)
        w.writerows(table)
    return len(rows)


def _mean_quality(models: list[str], quality: dict) -> float | None:
    if not models or not quality:
        return None
    return round(sum(quality.get(m, 0.0) for m in models) / len(models), 4)


def _ensemble_summary(crossmodel: Path, variant: str, ensemble_mode: str = "greedy") -> dict | None:
    """Reads {crossmodel}/{variant}/{ensemble_mode}/ensemble_summary.json —
    ensemble_mode "greedy" (set-cover heuristic), "ilp" (exact max-coverage via MILP),
    or "pareto_smaa"/"pareto_topsis" (the joint coverage-quality objective swept over
    lambda; see tasks/select_ensemble.py) — trimmed to the fields results.md prose cites:
    coverage curve endpoints, the recommended subset, and, for the pareto modes, the whole
    frontier. Also carries recommended_models (full list, not just the count) so a
    greedy-vs-ilp overlap count can be computed downstream without re-reading the JSON."""
    path = crossmodel / variant / ensemble_mode / "ensemble_summary.json"
    if not path.exists():
        return None
    summary = _load_json(path)
    out = {
        "objective": summary.get("objective"),
        "target": summary.get("target"),
        "total_gt_triples": summary.get("total_gt_triples"),
        "n_gt_ke": summary.get("n_gt_ke"),
        "n_grounded_targets": summary.get("n_grounded_targets"),
        "total_target_items": summary.get("total_target_items"),
        # the support threshold behind n_grounded_targets: an absolute model count, so it
        # means a different fraction of the panel on a 71-model pilot than on an 11-model
        # production panel — quoted wherever a joint-coverage figure is (response_plan §F)
        "grounded_params": summary.get("grounded_params"),
        "single_best_coverage_frac": summary.get("single_best_coverage_frac"),
        # curated-reference single-best, reported separately from the (joint) figure
        # above because the two can rank models differently — the manuscript quotes both
        # and must not transcribe either by hand
        "single_best_curated_n": summary.get("single_best_curated_n"),
        "single_best_curated_frac": summary.get("single_best_curated_frac"),
        "single_best_curated_models": summary.get("single_best_curated_models"),
        # full per-model curated ranking, so statements like "the tenth-best recovers 73"
        # are regenerable rather than recomputed ad hoc
        "per_model_curated": summary.get("per_model_curated"),
        "max_coverage_frac": summary.get("max_coverage_frac"),
        "recommended_coverage_frac": summary.get("recommended_coverage_frac"),
        "recommended_kec_frac": summary.get("recommended_kec_frac"),
        "recommended_grounded_frac": summary.get("recommended_grounded_frac"),
        "recommended_n_covered": summary.get("recommended_n_covered"),
        "n_ke_covered": summary.get("n_ke_covered"),
        "recommended_models": summary.get("recommended_models", []),
        "n_recommended_models": len(summary.get("recommended_models", [])),
        "n_candidate_models": summary.get("n_candidate_models"),
        "n_skipped_models": summary.get("n_skipped_models"),
        # per-Key-Event coverage detail (title, n_gt, n_covered, covering_models,
        # missed_ref_idx) — needed to regenerate which KEs are fully/partially/never
        # covered, rather than hand-picking examples from the raw product file
        "per_ke": summary.get("per_ke"),
        # run settings (smaa_link, smaa_topn, budget, ...) so each table row is traceable
        # to the configuration that produced it
        "params": summary.get("params"),
    }
    # pareto modes only: the lambda sweep is the frontier table in results.md, and the
    # adopted operating point is knee_lam — both quoted per-row, so both come from here.
    if summary.get("pareto_frontier"):
        out["knee_lam"] = summary.get("knee_lam")
        out["knee_mean_quality"] = summary.get("knee_mean_quality")
        out["pareto_frontier"] = [
            {
                "lam": r.get("lam"),
                "n_models": r.get("n_models"),
                "n_covered": r.get("n_covered"),
                "coverage_frac": r.get("coverage_frac"),
                "kec_frac": r.get("kec_frac"),
                "mean_quality": r.get("mean_quality"),
                "models": r.get("models", []),
            }
            for r in summary["pareto_frontier"]
        ]
    # per-model contribution at the adopted point — the @tbl:pareto-models columns
    if summary.get("per_model_contribution"):
        out["per_model_contribution"] = summary["per_model_contribution"]
    # k-fold cross-validation of the selection procedure (select_ensemble's crossval_folds).
    # Every other coverage field above is RESUBSTITUTION — the objective maximises coverage
    # of the curated triples and is then scored on those same triples — so the manuscript
    # cannot quote recommended_kec_frac as a generalisation estimate without this beside it.
    # Folds are over Key Events, and scoring is on held-out CURATED triples only.
    if summary.get("crossval", {}).get("per_lam"):
        cv = summary["crossval"]
        out["crossval"] = {
            "n_folds": cv.get("n_folds"),
            "seed": cv.get("seed"),
            "scored_on": cv.get("scored_on"),
            "per_lam": [
                {
                    "lam": r.get("lam"),
                    "in_sample_curated_frac": r.get("in_sample_curated_frac"),
                    "mean_curated_frac": r.get("mean_curated_frac"),
                    "sd_curated_frac": r.get("sd_curated_frac"),
                    "pooled_curated_frac": r.get("pooled_curated_frac"),
                    "optimism": r.get("optimism"),
                    "n_models_range": r.get("n_models_range"),
                }
                for r in cv["per_lam"]
            ],
        }
        # the adopted operating point's row, pulled out so prose can cite one number
        knee = next((r for r in out["crossval"]["per_lam"]
                     if r["lam"] == summary.get("knee_lam")), None)
        if knee:
            out["crossval_at_knee"] = knee
    return out


def _triple_tuple(t: dict) -> dict:
    return {
        "object": (t.get("biological_object") or {}).get("name"),
        "object_iri": (t.get("biological_object") or {}).get("iri"),
        "process": (t.get("biological_process") or {}).get("name"),
        "process_iri": (t.get("biological_process") or {}).get("iri"),
        "action": (t.get("biological_action") or {}).get("name"),
        "action_margin": (t.get("biological_action") or {}).get("margin"),
    }


def _worked_example_kec(products: Path, model_key: str, ke_id: str) -> dict | None:
    kec_path = products / model_key / "kec.json"
    match_path = products / model_key / "kec_match_report.json"
    if not kec_path.exists():
        logger.warning(f"worked example: missing {kec_path}")
        return None
    kec = _load_json(kec_path)
    entry = kec.get(ke_id)
    if entry is None:
        logger.warning(f"worked example: {ke_id} not present for {model_key}")
        return None
    llm_triples = (entry.get("llm_extracted") or {}).get("triples", [])
    match_verdicts = []
    if match_path.exists():
        match = _load_json(match_path)
        match_verdicts = match.get("per_ke", {}).get(ke_id, [])
    return {
        "title": entry.get("title"),
        "curated": entry.get("structured"),
        "llm_extracted": [_triple_tuple(t) for t in llm_triples],
        "match": match_verdicts,
    }


def _worked_example_assays(products: Path, model_key: str, ke_id: str) -> dict | None:
    assays_path = products / model_key / "assays.json"
    if not assays_path.exists():
        logger.warning(f"worked example (assays): missing {assays_path}")
        return None
    assays = _load_json(assays_path)
    recs = assays.get(ke_id)
    if recs is None:
        logger.warning(f"worked example (assays): {ke_id} not present for {model_key}")
        return None
    return {"assays": recs}


def _load_track_consistency(path: str | None) -> dict:
    """model_key -> ke_id -> [match results] from crossmodel_track_consistency.json's
    per_ke block (tasks/crossmodel_track_consistency.py). {} if not supplied/found."""
    if not path or not Path(path).exists():
        return {}
    return _load_json(Path(path)).get("per_ke", {})


def _consensus_assays_for_kes(path: str | None, ke_ids: list[str],
                              top_n: int = 6) -> dict | None:
    """Top cross-model-supported assays per Key Event, from viz_assay_consensus.json.

    The clustering is already done upstream (tasks/crossmodel_assays.py): each KE carries
    `clusters` with `support`, `models` and a representative `assay`. This only filters to
    the case-study KEs and orders by support — no re-matching, no re-derivation.

    This is what makes the AOP 451 assay table regenerable. It is the *panel* view, and it
    is not the same quantity as the per-model recall in `reference_check`: a single model
    proposes a small part of the published reference panel, while the panel collectively
    proposes much more, and the two tables have to be read as answering different
    questions rather than as a discrepancy."""
    if not path or not Path(path).exists():
        logger.warning(f"missing viz_assay_consensus: {path}")
        return None
    viz = _load_json(Path(path))
    n_panel = len((viz.get("summary") or {}).get("models") or [])
    wanted = set(ke_ids or [])
    out: dict = {}
    for ke in viz.get("kes", []):
        ke_id = ke.get("ke_id")
        if wanted and ke_id not in wanted:
            continue
        clusters = sorted(ke.get("clusters") or [],
                          key=lambda c: -(c.get("support") or 0))
        rows = []
        for c in clusters[:top_n]:
            assay = c.get("assay") or {}
            rows.append({
                "assay_name": assay.get("assay_name"),
                "measured_endpoint": assay.get("measured_endpoint"),
                "method_type": assay.get("method_type"),
                "guideline": assay.get("guideline"),
                "support": c.get("support"),
            })
        out[ke_id] = {"n_clusters": len(clusters), "top_assays": rows}
    return {"n_panel_models": n_panel, "per_ke": out}


def _reference_check(path: str | None) -> dict | None:
    """AOP 451 scored against the published manual curation, from
    tasks/aop451_reference_check.py's json product.

    The one place in this paper where extraction meets an *outside* reference: the
    pathway's curated triples and its 19 recommended standardized assays, transcribed
    from Nymark et al. (2021) Figure 1 into resources/aop451_reference.json. Both recall
    figures the case study quotes come from here rather than being counted by hand, and
    the per-KE breakdown behind them is carried through so the manuscript table is
    regenerable when the panel or the release changes.

    Recall against a curated reference, NOT the curated-agreement rate the pilot reports:
    this is matched ÷ curated, the pilot's is matched ÷ proposed. The two are not
    comparable and the case study has to say which it means."""
    if not path or not Path(path).exists():
        logger.warning(f"missing aop451 reference check: {path}")
        return None
    data = _load_json(Path(path))
    out = {
        "reference": data.get("reference"),
        "aop_wiki_id": data.get("aop_wiki_id"),
        "ke_ids": data.get("ke_ids", []),
        # the pooled panel scored against the same reference, same rule, same tau —
        # this is the "Panel" column of the case-study table, not a hand count
        "consensus_assays": data.get("consensus_assays"),
        "per_model": {},
    }
    for model, res in (data.get("results") or {}).items():
        kec = res.get("kec") or {}
        assays = res.get("assays") or {}
        out["per_model"][model] = {
            "kec_n_curated": kec.get("n_curated_total"),
            "kec_n_matched": kec.get("n_matched_total"),
            "kec_recall": kec.get("recall"),
            "kec_per_ke": kec.get("per_ke"),
            "assay_n_reference": assays.get("n_reference_assays"),
            "assay_n_recovered": assays.get("n_recovered"),
            "assay_recovery_rate": assays.get("recovery_rate"),
            "assay_detail": assays.get("assays"),
        }
    return out


def _technique_stats(path: str | None) -> dict | None:
    """Technique-vs-record counts and the most-reused techniques, from
    the publish stage's `stats` product (not part of this repo).

    The crossmodel stage counts assay records *per Key Event*: one technique described in
    twenty Key Events is twenty records. Name consolidation into distinct techniques only
    happens on the Solr path, so that is where the two counts exist together and this is
    read rather than re-derived. Both depend on assay_min_support, since consolidation
    runs after that gate."""
    if not path or not Path(path).exists():
        logger.warning(f"missing assay technique stats: {path}")
        return None
    stats = _load_json(Path(path))
    return {
        "assay_min_support": stats.get("assay_min_support"),
        "n_records_indexed": stats.get("n_records_indexed"),
        "n_techniques": stats.get("n_techniques"),
        "n_ke_covered": stats.get("n_ke_covered"),
        "records_per_technique_mean": stats.get("records_per_technique_mean"),
        "n_techniques_single_ke": stats.get("n_techniques_single_ke"),
        "n_techniques_multi_ke": stats.get("n_techniques_multi_ke"),
        "max_ke_per_technique": stats.get("max_ke_per_technique"),
        "reuse_examples": stats.get("reuse_examples", []),
    }


def _track_consistency_summary(path: str | None) -> dict | None:
    """Corpus-wide KEC-vs-assay cross-track agreement (R1 §5.3 / response_plan D5), read
    from crossmodel_track_consistency.json's `summary` block.

    This is the one signal neither track produces alone and no query over AOP-Wiki's XML
    can produce at all: the KE description and the measurement_methodology field are
    different free texts, read under different schemas by different prompts, so agreement
    between an assay's implied (object, process) and the KE's own KEC triple is two
    independent structurings converging — corroboration with no curated reference
    anywhere in it. Reported corpus-wide rather than only for the worked examples.

    Two rules travel together and must not be conflated: the primary number matches on
    object-or-process, the strict one additionally requires the action to agree. Action
    routinely differs for a legitimate reason — an assay's direction comes from its own
    methodology text, so one KE's assays can report both increase and decrease of the same
    entity — which is why the primary rule, not the strict one, is the evidence that the
    two tracks describe the same biology."""
    if not path or not Path(path).exists():
        logger.warning(f"missing track_consistency json: {path}")
        return None
    summary = _load_json(Path(path)).get("summary") or {}
    per_model = summary.get("per_model") or {}
    scored = {mk: rec for mk, rec in per_model.items()
              if rec.get("n_assay_triples_scored")}
    # Panel-level rates are pooled over assay triples (sum of matches / sum of scored),
    # not a mean of per-model rates, so models contributing more assays weigh more.
    n_scored = sum(r["n_assay_triples_scored"] for r in scored.values())
    n_matched = sum(r["n_matched"] for r in scored.values())
    n_matched_strict = sum(r["n_matched_strict"] for r in scored.values())
    return {
        "matching_rule": summary.get("matching_rule"),
        "tau": summary.get("tau"),
        "n_models_scored": len(scored),
        "n_ke_common_max": max((r["n_ke_common"] for r in scored.values()), default=0),
        "n_ke_scored_max": max((r["n_ke_scored"] for r in scored.values()), default=0),
        "n_assay_triples_scored": n_scored,
        "n_matched": n_matched,
        "n_matched_strict": n_matched_strict,
        "consistency_pooled": (n_matched / n_scored) if n_scored else None,
        "consistency_pooled_strict": (n_matched_strict / n_scored) if n_scored else None,
        "per_model": {mk: {
            "n_ke_scored": r["n_ke_scored"],
            "n_assay_triples_scored": r["n_assay_triples_scored"],
            "cross_track_consistency": r["cross_track_consistency"],
            "cross_track_consistency_strict": r["cross_track_consistency_strict"],
        } for mk, r in scored.items()},
    }


def _worked_examples(
    kec_products: Path | None, assays_products: Path | None,
    track_consistency_per_ke: dict | None = None,
) -> dict:
    """Pull per-KE curated + LLM-extracted KEC detail, and separately per-KE assay
    detail, for each (KE, model) pair — from two independent products roots, since the
    KEC and assay tracks can be on different AOP-Wiki releases and are not to be mixed.
    Either root may be omitted (None) to pull only the other track's data. When
    track_consistency_per_ke is given, also attach that (model, KE)'s cross-track match
    detail (D5) — one entry per scored assay, each with matched/matched_strict/method."""
    out = {}
    for ke_id in worked_example_kes:
        ke_out = {}
        for model_key in worked_example_models:
            model_out = {}
            if kec_products is not None:
                kec = _worked_example_kec(kec_products, model_key, ke_id)
                if kec is not None:
                    model_out["kec"] = kec
            if assays_products is not None:
                assays = _worked_example_assays(assays_products, model_key, ke_id)
                if assays is not None:
                    model_out["assays"] = assays
            if track_consistency_per_ke:
                tc = (track_consistency_per_ke.get(model_key) or {}).get(ke_id)
                if tc is not None:
                    model_out["track_consistency"] = tc
            if model_out:
                ke_out[model_key] = model_out
        if ke_out:
            out[ke_id] = ke_out
    return out


def _both_slots_grounded(triple: dict) -> bool:
    """A candidate is usable as an annotation only if BOTH the object and the process slot
    carry an ontology identifier — the same two-slot test the grounded-novel target uses in
    select_ensemble.py. A name without an accession still needs a curator to ground it."""
    obj = (triple.get("biological_object") or {}).get("iri")
    proc = (triple.get("biological_process") or {}).get("iri")
    return bool(obj) and bool(proc)


def _consensus_recovery(crossmodel: Path, min_support: int | None) -> dict | None:
    """Curated recovery AND novel-candidate yield, straight from crossmodel_consensus.json.

    Two populations, split by the `in_ground_truth` flag each cluster already carries:
    clusters that a curated triple joined (how much of the existing layer the pooled
    extraction reproduces) and clusters that match nothing curated (the candidate new
    annotations the workflow exists to produce).

    NOTE: these count CLUSTERS, not curated triples, and the two differ in both directions.
    Several curated triples for one Key Event can merge into a single cluster under the
    hybrid matcher, and crossmodel_compare drops any cluster with no real-model support, so
    a curated triple no model reproduced never appears here at all. Do not divide these by
    the curated-triple total to get a recovery rate — for triple-level coverage use the
    ensemble block's curated figures, which count triples. Both are reported by support
    level, since
    support is the only quality signal available for the novel population — there is no
    reference to check it against — and `both_iri` counts the subset whose object and
    process slots are both ontology-grounded, i.e. usable without a curator supplying an
    accession first.

    Deliberately independent of ensemble selection: this is a property of the run's own
    output (all models that were run), not of any selected subset, so a section reporting
    extraction results can cite it without importing coverage/selection reasoning.

    The novel population is NOT a second ground truth — a cluster matching nothing curated
    may be a genuine gap in the curated layer or may be wrong, and nothing here
    distinguishes the two. Cross-model support is corroboration, not proof."""
    path = crossmodel / "crossmodel_consensus.json"
    if not path.exists():
        logger.warning(f"missing crossmodel_consensus.json: {path}")
        return None
    per_ke = _load_json(path)

    curated_by_support: dict[int, int] = {}
    novel_by_support: dict[int, int] = {}
    novel_grounded_by_support: dict[int, int] = {}
    ontology_sources: dict[str, int] = {}
    n_ke_with_gt = 0
    ke_with_novel: set[str] = set()
    # KEs whose ONLY candidate evidence is single-model: nothing there clears the support
    # threshold, so a consensus-filtered catalogue drops these Key Events entirely. The
    # singleton population is otherwise invisible in the reported numbers even though it
    # is the largest one — and `curated_by_support[1]` shows it demonstrably contains
    # genuine annotations (curated triples that exactly one model found).
    ke_with_novel_any: set[str] = set()

    for ke_id, clusters in per_ke.items():
        if any(c.get("in_ground_truth") for c in clusters):
            n_ke_with_gt += 1
        for c in clusters:
            support = int(c.get("support") or 0)
            if c.get("in_ground_truth"):
                curated_by_support[support] = curated_by_support.get(support, 0) + 1
                continue
            novel_by_support[support] = novel_by_support.get(support, 0) + 1
            ke_with_novel_any.add(ke_id)
            if min_support is not None and support >= min_support:
                ke_with_novel.add(ke_id)
            if _both_slots_grounded(c.get("triple") or {}):
                novel_grounded_by_support[support] = (
                    novel_grounded_by_support.get(support, 0) + 1)
                if min_support is not None and support >= min_support:
                    for slot in ("biological_object", "biological_process"):
                        src = ((c.get("triple") or {}).get(slot) or {}).get("source")
                        if src:
                            ontology_sources[src] = ontology_sources.get(src, 0) + 1

    def _at_least(hist: dict[int, int], threshold: int | None) -> int | None:
        if threshold is None:
            return None
        return sum(n for s, n in hist.items() if s >= threshold)

    return {
        # curated layer reproduced
        "n_curated_clusters_all": sum(curated_by_support.values()),
        "n_curated_clusters_at_threshold": _at_least(curated_by_support, min_support),
        "n_ke_with_curated_match": n_ke_with_gt,
        "curated_support_histogram": {str(k): v for k, v in sorted(curated_by_support.items())},
        # candidate new annotations — the workflow's actual output
        "n_novel_clusters_all": sum(novel_by_support.values()),
        "n_novel_clusters_at_threshold": _at_least(novel_by_support, min_support),
        "n_novel_grounded_at_threshold": _at_least(novel_grounded_by_support, min_support),
        "n_ke_with_novel_at_threshold": len(ke_with_novel) if min_support else None,
        "n_ke_with_novel_any_support": len(ke_with_novel_any),
        # KEs that have candidates but none surviving the threshold — lost entirely if the
        # catalogue is filtered on consensus, so the number a curator-hint use case needs
        "n_ke_novel_only_below_threshold": (
            len(ke_with_novel_any - ke_with_novel) if min_support else None),
        "novel_support_histogram": {str(k): v for k, v in sorted(novel_by_support.items())},
        "novel_grounded_support_histogram": {
            str(k): v for k, v in sorted(novel_grounded_by_support.items())},
        "novel_ontology_sources": dict(
            sorted(ontology_sources.items(), key=lambda kv: -kv[1])),
    }


def _assay_model_summary(crossmodel: Path) -> dict | None:
    """Per-model evidence-grounding / IRI-validity / cross-track figures, from
    assay_model_summary_all.csv (tasks/assay_model_summary.py) — the assay track's
    equivalent of the KEC per_model block, but for criteria that have no crossmodel
    equivalent (evidence_grounded_frac, iri_valid_frac are computed per model against
    each model's own assays.json, not from consensus clusters).

    Reads the whole CSV and reports both per-model rows and the panel min/max for the
    two headline reference-free criteria, so a claim like "the weakest model resolves
    28.5% of its IRIs" is sourced here rather than read off the CSV by eye."""
    path = crossmodel / "assay_model_summary_all.csv"
    if not path.exists():
        logger.warning(f"missing assay_model_summary_all.csv: {path}")
        return None
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    if not rows:
        return None

    def _num(row, key):
        v = row.get(key)
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None

    per_model = {}
    for r in rows:
        per_model[r["model_key"]] = {
            "evidence_grounded_frac": _num(r, "evidence_grounded_frac"),
            "iri_valid_frac": _num(r, "iri_valid_frac"),
            "consensus_agreement": _num(r, "consensus_agreement"),
            "direction_margin_auc": _num(r, "direction_margin_auc"),
            "cross_track_consistency": _num(r, "cross_track_consistency"),
            "n_assays": _num(r, "n_assays"),
        }

    def _range(field):
        vals = [v[field] for v in per_model.values() if v[field] is not None]
        if not vals:
            return None
        lo_model = min(per_model, key=lambda m: (per_model[m][field]
                       if per_model[m][field] is not None else float("inf")))
        hi_model = max(per_model, key=lambda m: (per_model[m][field]
                       if per_model[m][field] is not None else float("-inf")))
        return {"min": min(vals), "min_model": lo_model,
                "max": max(vals), "max_model": hi_model}

    return {
        "per_model": per_model,
        "evidence_grounded_frac_range": _range("evidence_grounded_frac"),
        "iri_valid_frac_range": _range("iri_valid_frac"),
    }


def _crossmodel_overview(crossmodel: Path) -> dict | None:
    path = crossmodel / "crossmodel_summary.json"
    if not path.exists():
        logger.warning(f"missing crossmodel_summary.json: {path}")
        return None
    summary = _load_json(path)["summary"]
    overview = {
        "n_ke_union": summary.get("n_ke_union"),
        "n_clusters": summary.get("n_clusters"),
        "n_consensus_clusters": summary.get("n_consensus_clusters"),
        "min_support": summary.get("min_support"),
        "tau": summary.get("tau"),
        "embedding_model": summary.get("embedding_model"),
        "n_models_present": len(summary.get("models_present", [])),
        "support_histogram": summary.get("support_histogram"),
    }
    recovery = _consensus_recovery(crossmodel, summary.get("min_support"))
    if recovery:
        overview.update(recovery)
    return overview


if enabled:
    assert manuscript_repo and section_name, (
        "params 'manuscript_repo', 'section_name' are required")

    repo = Path(manuscript_repo)
    figures_out = repo / "figures" / section_name

    out = {"section": section_name, "figures": []}

    if crossmodel_dir:
        crossmodel = Path(crossmodel_dir)
        logger.info(f"section={section_name} crossmodel={crossmodel}")

        copied = _copy_figures(crossmodel, figures_out)
        overview = _crossmodel_overview(crossmodel)
        smaa = {}
        ensemble = {}
        for variant in smaa_variants:
            stats = _smaa_variant_stats(crossmodel, variant)
            if stats is not None:
                smaa[variant] = stats
            variant_ensembles = {}
            # pareto_smaa is the adopted selection (results.md @tbl:pareto); pareto_topsis
            # is the alternative-quality-score comparison the same section reports.
            for mode in ENSEMBLE_MODES:
                ens = _ensemble_summary(crossmodel, variant, mode)
                if ens is not None:
                    variant_ensembles[mode] = ens
            # @tbl:ensemble-strategies: every strategy's quality on one common scale, and
            # its overlap with plain greedy - computed here, not in select_ensemble, because
            # greedy/ilp never load quality and no single run sees the other runs' picks.
            q_topsis = _panel_quality(crossmodel, variant, "topsis_score")
            q_smaa = _panel_quality(crossmodel, variant, "expected_rank")
            base = variant_ensembles.get("greedy_none") or variant_ensembles.get("greedy")
            base_models = set(base["recommended_models"]) if base else set()
            for mode, ens in variant_ensembles.items():
                rec = ens["recommended_models"]
                ens["mean_quality_topsis"] = _mean_quality(rec, q_topsis)
                ens["mean_quality_smaa"] = _mean_quality(rec, q_smaa)
                if base_models:
                    ens["n_overlap_greedy"] = len(set(rec) & base_models)
                    ens["n_greedy"] = len(base_models)
                for row in ens.get("pareto_frontier", []):
                    row["mean_quality_topsis"] = _mean_quality(row["models"], q_topsis)
                    if base_models:
                        row["n_overlap_greedy"] = len(set(row["models"]) & base_models)
            if "greedy" in variant_ensembles and "ilp" in variant_ensembles:
                g_models = set(variant_ensembles["greedy"]["recommended_models"])
                i_models = set(variant_ensembles["ilp"]["recommended_models"])
                variant_ensembles["greedy_vs_ilp_overlap"] = {
                    "n_overlap": len(g_models & i_models),
                    "n_greedy": len(g_models),
                    "n_ilp": len(i_models),
                }
            if variant_ensembles:
                ensemble[variant] = variant_ensembles

        # supplementary panel table: only for a section whose product declares "panel"
        if "panel" in product:
            adopted = (ensemble.get("default", {}).get("pareto_smaa") or {}).get(
                "recommended_models", [])
            n = _write_panel_table(crossmodel, "default", adopted, Path(product["panel"]))
            logger.info(f"panel table: {n} models -> {product['panel']}")

        lineage = _lineage_jaccard(crossmodel)
        if lineage:
            out["lineage_jaccard"] = lineage
            logger.info(f"lineage Jaccard: {len(lineage['pairs'])} pairs vs "
                        f"{lineage['distribution']['n_pairs']} panel pairs")

        out["source"] = str(crossmodel)
        out["crossmodel_overview"] = overview
        out["smaa"] = smaa
        out["ensemble"] = ensemble
        out["figures"] = copied

        if onto_recovery_json and Path(onto_recovery_json).exists():
            out["onto_recovery"] = _load_json(Path(onto_recovery_json))

        # Assay-track only: evidence grounding / IRI validity are per-model, computed
        # against each model's own assays.json, not from crossmodel's consensus clusters
        # — no equivalent file exists for the KEC track, so this is None there.
        assay_summary = _assay_model_summary(crossmodel)
        if assay_summary:
            out["assay_model_summary"] = assay_summary

        # Corpus-wide cross-track agreement, when this section was given the
        # crossmodel_track_consistency.json for its own panel (assays track).
        tc_summary = _track_consistency_summary(track_consistency_json)
        if tc_summary:
            out["track_consistency"] = tc_summary
            logger.info(
                f"track consistency: {tc_summary['n_models_scored']} models, "
                f"{tc_summary['n_assay_triples_scored']} assay triples scored, "
                f"pooled={tc_summary['consistency_pooled']}")

        tech = _technique_stats(technique_stats_json)
        if tech:
            out["technique_stats"] = tech
            logger.info(
                f"techniques: {tech['n_techniques']} distinct from "
                f"{tech['n_records_indexed']} per-KE records")
    else:
        figures_out.mkdir(parents=True, exist_ok=True)
        logger.info(f"section={section_name} {crossmodel_dir} (no crossmodel_dir — worked examples only)")

    if worked_example_kes and worked_example_models and (
            products_dir or assays_products_dir):
        kec_root = Path(products_dir) if products_dir else None
        assays_root = Path(assays_products_dir) if assays_products_dir else None
        tc_per_ke = _load_track_consistency(track_consistency_json)
        worked = _worked_examples(kec_root, assays_root, tc_per_ke)
        out["worked_examples"] = worked
        logger.info(f"worked examples: {len(worked)}/{len(worked_example_kes)} KEs resolved"
                    + (f", track_consistency for {len(tc_per_ke)} models" if tc_per_ke else ""))

    # Publish the assay stage's raw technique stats next to the other generated files
    # (verbatim copy; the summarised subset in out["technique_stats"] is unchanged).
    published = publish_technique_stats(technique_stats_json, repo)
    if published:
        logger.info(f"published technique stats -> {published}")

    # Independent of crossmodel_dir: scored per model against the published curation,
    # not against the panel, so it is read whether or not this section has a crossmodel.
    ref = _reference_check(reference_check_json)
    if ref:
        out["reference_check"] = ref
        for model, r in ref["per_model"].items():
            logger.info(
                f"reference check {model}: KEC {r['kec_n_matched']}/{r['kec_n_curated']}, "
                f"assays {r['assay_n_recovered']}/{r['assay_n_reference']}")

    cons = _consensus_assays_for_kes(viz_assay_consensus_json, worked_example_kes)
    if cons:
        out["consensus_assays"] = cons
        logger.info(f"consensus assays: {len(cons['per_ke'])} KEs, "
                    f"panel of {cons['n_panel_models']}")

    out_path = Path(product["json"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    n_worked = len(out.get("worked_examples", {}))
    logger.info(f"wrote {out_path}: {len(out['figures'])} figures, "
                f"{len(out.get('smaa', {}))}/{len(smaa_variants)} SMAA variants, "
                f"{n_worked} worked-example KEs")
else:
    logger.info("export_manuscript_results disabled — skipping")
