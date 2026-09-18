"""
select_ensemble.py — Ploomber task: pick a *subset* of models whose combined
extractions best reproduce the curated (KEC-endorsed) triples.

`smaa_rank.py` ranks each model in isolation (one scalar per model) and throws the
Key-Event axis away. That answers "which single model is best," not "which set of
models, together, recovers the most endorsed triples." Because different models
recover different endorsed triples, the union of a well-chosen subset out-covers any
single model — a **maximum-coverage / set-cover** problem.

Approach (see docs/ensemble_selection_methods.md):
  • GT universe G = {(ke_id, ref_idx)} from kec.json[ke]["structured"] (same for all
    models). A model m covers (ke_id, ref_idx) iff its kec_match_report.json has a
    matched record with that ref_idx — the per-triple verdict from compare_kec.
  • Greedy: repeatedly add the model contributing the most previously-uncovered
    endorsed triples. Marginal gains are non-increasing (submodularity), so the
    cumulative coverage curve has a natural elbow. Runs to zero gain → full curve.
  • Recommended subset = leading prefix where every added model contributes at least
    `min_gain` new endorsed triples (capped by `budget`).
  • We optimise coverage (recall of endorsed content), NOT F-score: a triple matching
    no GT is often a novel candidate worth curating, not an error. Un-endorsed
    ("novel") triples are reported descriptively (pooled precision), not penalised.
  • Per-KE report: for the recommended subset, which endorsed triples were recovered
    and by which models — surfacing weakly-covered KEs and pivotal models.

Alternative coverage target (`target = "kec_plus_grounded"`, off by default):
  KEC endorsement is a curation snapshot, not absolute ground truth. A novel triple
  (no KEC match) that several models independently agree on AND whose object+process
  terms are ontology-grounded (ground_kec match) is a plausible curation gap, not an
  error. In this mode the universe is KEC-endorsed triples ∪ grounded-novel consensus
  clusters, and greedy maximizes coverage of the union. KEC coverage is ALWAYS reported
  separately, so the endorsed-vs-grounded split stays legible; `target = "kec"` is
  byte-for-byte unchanged (the grounded machinery is skipped entirely).

Selection mode (`ensemble_mode`, off the default "greedy"):
  "ilp" is a second, fully independent selection mode — not a check run against the
  greedy result. It solves the identical max-coverage problem exactly via mixed-integer
  linear programming (scipy.optimize.milp / HiGHS), over the SAME universe `target`
  selects for greedy (KEC-only for target=="kec"; KEC ∪ grounded-novel for
  target=="kec_plus_grounded" — see _solve_ilp), ignoring the greedy-specific
  smaa_link/smaa_topn/min_gain params, and writes its own complete "tsv"/"json"/"fig"
  products. per_model_contribution stays KEC-only regardless of target (see its own
  comment) so the per-model breakdown is always anchored to curated ground truth.
  Independent of smaa_rank's `ranking` param, which only selects a SMAA criteria block
  and plays no role here.

Ploomber:
  upstream: ["crossmodel_compare", "smaa_rank"]  (reads per-model kec_match_report.json
            from disk; crossmodel_compare's `consensus` product in grounded mode;
            smaa_rank's `json` product when smaa_link != none — path taken from upstream)
  product:  {"tsv": greedy steps, "json": summary+per_ke, "fig": coverage curve png}
  params:   products, model_keys, min_gain, budget; optional smaa_link (none|tiebreak|
            prefilter) + smaa_topn to couple with the SMAA ranking (its summary path is
            read from the smaa_rank upstream, not passed);
            target (kec|kec_plus_grounded) + grounded_match_levels + grounded_min_support;
            ensemble_mode (greedy|ilp) — see above.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["crossmodel_compare"]
product = None
enabled = True
products = "products_202606"
model_keys = None                       # None → use consensus models_present
min_gain = 1                            # recommended cut: keep adding while a model
                                        # contributes ≥ this many new GT triples
budget = None                           # optional hard cap on subset size (None = none)
# Optional coupling with the SMAA ranking (off by default):
#   none      — ignore SMAA; tie-break greedy deterministically by model name
#   tiebreak  — on equal marginal gain, prefer the higher SMAA first_rank_prob
#   prefilter — restrict candidates to the top-`smaa_topn` SMAA models, then cover
smaa_link = "none"
smaa_topn = 15
consensus_upstream = "crossmodel_compare"   # upstream key holding the crossmodel summary
smaa_upstream = "smaa_rank"                  # upstream key holding smaa_summary.json; its
                                            # path is read from the product, only when
                                            # smaa_link != "none"
# Alternative coverage target (off by default; see module docstring):
#   kec               — cover only KEC-endorsed triples (unchanged)
#   kec_plus_grounded — also cover novel consensus clusters whose object AND process
#                       terms are ontology-grounded (ground_kec match)
target = "kec"
grounded_match_levels = ["aopwiki", "exact"]   # a term is grounded iff its match ∈ this
grounded_min_support = 2                        # grounded-novel target needs ≥ this many
                                                # grounded members (keeps it a consensus)
# Selection mode — independent of smaa_rank's `ranking` (that picks a SMAA criteria
# block; this picks how select_ensemble itself decides). Three mutually exclusive modes:
#
#   "greedy"  — the default set-cover heuristic (coverage only).
#   "ilp"     — the identical coverage problem solved exactly (scipy.optimize.milp /
#               HiGHS). Same objective as greedy, no approximation gap.
#   "pareto"  — coverage AND per-model SMAA quality as competing terms in ONE objective:
#               maximize (covered targets) - lam * sum over selected models of (1 - q_m),
#               solved exactly for each lam in `pareto_lambdas` (see _solve_ilp). q_m is
#               a SMAA-derived per-model quality score (`quality_column`, min-max scaled
#               across the panel). The result is the Pareto frontier over the sweep, not
#               a single chosen lam.
#
# Why "pareto" exists. Pure coverage maximisation ("greedy"/"ilp") has no precision term
# at all, so it will happily rank first a model that recovers many curated triples while
# proposing far more that match nothing — a real review burden for a curation pipeline
# that the objective cannot see. The two existing SMAA couplings do not fix this and fail
# for opposite reasons: smaa_link="prefilter" applies quality BEFORE the objective (the
# discarded models are invisible to it, so coverage is lost with nothing measurable bought),
# and smaa_link="tiebreak" applies quality AFTER it (arbitrating only where coverage is
# indifferent, so by construction it can never overrule the coverage-driven picks you would
# most want it to). Quality has to be a term the optimiser trades against coverage on every
# selection decision, which is what this mode does.
#
# lam is a stated exchange rate between coverage and quality, not a hidden threshold: it is
# swept over `pareto_lambdas` and the whole frontier is reported, so the deliverable is
# "here is what each unit of coverage costs in panel quality" rather than a tuned constant.
ensemble_mode = "greedy"
# lam values to sweep in any "pareto*" mode. 0.0 reproduces pure max-coverage (the "ilp"
# result) and anchors one end of the frontier; large values drive selection toward the
# highest-quality models regardless of coverage. Each lam is one fast exact re-solve.
pareto_lambdas = [0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]
# which smaa_criteria.tsv column supplies q_m (see _load_quality/QUALITY_DIRECTION), when
# ensemble_mode is bare "pareto" — ignored for "pareto_topsis"/"pareto_smaa", which set
# their own column via PARETO_MODE_COLUMN. "topsis_score" combines every active SMAA
# criterion via entropy weights, so nothing here is hand-picked.
quality_column = "topsis_score"
# -

try:
    from tasks.logutil import setup_logging
    logger = setup_logging(Path(product["json"]).parent / "select_ensemble.log")
except Exception:                       # standalone / import-safe fallback
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("select_ensemble")


# ── data loading ──────────────────────────────────────────────────────────────

def _load_gt(prod: Path, present: list[str]) -> tuple[dict, dict]:
    """GT universe: {ke_id: n_gt} and {ke_id: title}, from the first kec.json found
    (structured GT is identical across models). A KE has GT iff `structured` is
    non-empty."""
    for mk in present:
        p = prod / mk / "kec.json"
        if not p.exists():
            continue
        kec = json.loads(p.read_text(encoding="utf-8"))
        gt_counts, titles = {}, {}
        for ke_id, rec in kec.items():
            struct = rec.get("structured") or []
            if struct:
                gt_counts[ke_id] = len(struct)
                if rec.get("title"):
                    titles[ke_id] = rec["title"]
        return gt_counts, titles
    return {}, {}


def _load_covered(prod: Path, mk: str, gt_counts: dict) -> tuple[set, int, int] | None:
    """For model mk: the set of (ke_id, ref_idx) GT triples it recovers, plus pooled
    (n_matched, n_pred) for the descriptive precision column. Mirrors the ref_idx
    inversion in smaa_rank._gt_coverage and the viz_consensus loader."""
    path = prod / mk / "kec_match_report.json"
    if not path.exists():
        return None
    rep = json.loads(path.read_text(encoding="utf-8"))
    covered: set = set()
    n_pred = n_matched = 0
    for ke_id, matches in (rep.get("per_ke") or {}).items():
        if ke_id not in gt_counts:
            continue
        for m in matches:
            n_pred += 1
            if m.get("matched"):
                n_matched += 1
                ri = m.get("ref_idx")
                if ri is not None and ri < gt_counts[ke_id]:
                    covered.add((ke_id, ri))
    # prefer the report's own summary counts when present (counts all KEs, incl. no-GT)
    summ = rep.get("summary") or {}
    n_pred = int(summ.get("n_pred_triples") or n_pred)
    n_matched = int(summ.get("n_matched") or n_matched)
    return covered, n_matched, n_pred


def _load_smaa_rank(path: str | None) -> dict:
    """{model: first_rank_prob} from a smaa_summary.json, for tie-break/pre-filter."""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        logger.warning(f"smaa_link={smaa_link} but {p} not found — falling back to none")
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {r["model"]: float(r.get("first_rank_prob") or 0.0)
            for r in data.get("ranking", []) if r.get("model")}


# ── grounded-novel target loading (only when target == "kec_plus_grounded") ─────

def _load_grounding(prod: Path, present: list[str], levels: set) -> dict:
    """{(model, ke_id, triple_idx): bool} — True iff BOTH the object and process terms
    of that predicted triple are ontology-grounded (ground_kec's grounding.match ∈
    levels). Built from each present model's kec_grounded.json; missing files skipped
    with a warning. Positionally joinable to consensus cluster members by
    (model, ke_id, triple_idx)."""
    out: dict = {}
    missing = []
    for mk in present:
        p = prod / mk / "kec_grounded.json"
        if not p.exists():
            missing.append(mk)
            continue
        kg = json.loads(p.read_text(encoding="utf-8"))
        for ke_id, rec in kg.items():
            tris = ((rec.get("llm_extracted") or {}).get("triples")) or []
            for i, t in enumerate(tris):
                om = ((t.get("biological_object") or {}).get("grounding") or {}).get("match")
                pm = ((t.get("biological_process") or {}).get("grounding") or {}).get("match")
                out[(mk, ke_id, i)] = (om in levels) and (pm in levels)
    if missing:
        logger.warning(f"{len(missing)} model(s) without kec_grounded.json — their "
                       f"members count as ungrounded: " + ", ".join(missing))
    return out


def _load_grounded_targets(consensus_path: str, grounding: dict, min_sup: int) -> dict:
    """Read crossmodel_consensus.json; return {target_id: set(covering_models)} for each
    novel cluster (in_ground_truth == False) with ≥ min_sup grounded members. target_id
    = ("G", ke_id, cluster_index). A model covers the target iff it is a cluster member
    (it contributed to the agreed relationship), mirroring KEC coverage semantics."""
    p = Path(consensus_path)
    if not p.exists():
        logger.warning(f"target=kec_plus_grounded but {p} not found — no grounded targets")
        return {}
    cons = json.loads(p.read_text(encoding="utf-8"))
    targets: dict = {}
    for ke_id, clusters in cons.items():
        for ci, c in enumerate(clusters):
            if c.get("in_ground_truth"):
                continue
            n_grounded = sum(1 for m in (c.get("members") or [])
                             if grounding.get((m.get("model"), ke_id, m.get("triple_idx"))))
            if n_grounded >= min_sup:
                targets[("G", ke_id, ci)] = set(c.get("models") or [])
    return targets


#: quality columns of smaa_criteria.tsv usable as the joint objective's q_m, and whether
#: higher or lower is better. Both are computed by smaa_rank.py over the SAME criteria
#: matrix but by DIFFERENT methods, and they are not interchangeable:
#:   topsis_score   deterministic entropy-weighted TOPSIS closeness, higher = better. One
#:                  value per model spread over the full [0,1] range, so it discriminates
#:                  across the whole panel.
#:   expected_rank  SMAA's own Monte-Carlo expected rank (1 = best), lower = better. Keeps
#:                  SMAA's criterion-weight uncertainty propagation, and unlike SMAA's
#:                  headline first_rank_prob it stays informative down the panel
#:                  (first_rank_prob is 0 for most models of a large panel, so as a
#:                  penalty it cannot tell mediocre from terrible — do not use it here).
QUALITY_DIRECTION = {"topsis_score": "higher", "expected_rank": "lower",
                     "first_rank_prob": "higher"}

#: ensemble_mode name-suffix -> quality_column, for the two supported "pareto_*" variants
#: (see ensemble_mode param docstring). "pareto_topsis" and "pareto_smaa" are self-
#: descriptive names for what would otherwise just be "which column" — TOPSIS vs SMAA's
#: own expected-rank output, two different scoring methods, not two names for one thing.
PARETO_MODE_COLUMN = {"pareto_topsis": "topsis_score", "pareto_smaa": "expected_rank"}


def _load_quality(smaa_summary_path: str | None, column: str) -> dict:
    """{model: quality in [0,1], higher = better} from smaa_criteria.tsv, written by
    smaa_rank.py next to its `json` product. `column` is any numeric column of that file;
    see QUALITY_DIRECTION for the two intended choices and why they differ. Values are
    min-max rescaled to [0,1] across the panel — and inverted first when the column is
    lower-is-better — so the lam penalty means the same thing whichever is chosen. A
    constant column maps to all-1.0 (no discrimination, hence no penalty)."""
    if not smaa_summary_path:
        return {}
    p = Path(smaa_summary_path).parent / "smaa_criteria.tsv"
    if not p.exists():
        logger.warning(f"quality: {p} not found — no per-model quality available")
        return {}
    raw = {}
    with open(p, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            mk = row.get("model")
            val = row.get(column)
            if not mk or val in (None, ""):
                continue
            try:
                raw[mk] = float(val)
            except ValueError:
                continue
    if not raw:
        logger.warning(f"quality: column {column!r} not usable in {p}")
        return {}
    direction = QUALITY_DIRECTION.get(column, "higher")
    if direction == "lower":
        raw = {m: -v for m, v in raw.items()}     # invert so higher is always better
    lo, hi = min(raw.values()), max(raw.values())
    if hi <= lo:
        return {m: 1.0 for m in raw}
    return {m: (v - lo) / (hi - lo) for m, v in raw.items()}


def _solve_ilp(covered: dict, universe: set, budget: int | None,
               quality: dict | None = None, lam: float = 0.0) -> tuple[list, int, str]:
    """Exact maximum-coverage ILP (scipy.optimize.milp / HiGHS — already a scipy
    dependency, nothing new to install). A genuine second selection mode alongside
    greedy, not a check run against a greedy result: solves the SAME coverage problem
    greedy solves over `universe`, but exactly rather than heuristically. `universe` is
    whatever the caller passes — KEC-only (target=="kec") or KEC ∪ grounded-novel
    (target=="kec_plus_grounded") — mirroring greedy's target handling exactly, so both
    modes optimise the identical objective for a given target and are directly comparable.

    variables:  x_m in {0,1} per candidate model, y_t in {0,1} per target in `universe`
    maximize:   sum_t y_t  -  lam * sum_m (1 - q_m) * x_m
    subject to: y_t <= sum_{m: t in covered(m)} x_m for every t;  sum_m x_m <= budget

    With lam == 0 (the default) this is pure maximum coverage — the "ilp" mode. With
    lam > 0 and `quality` = {model: q in [0,1]} (a SMAA-derived per-model quality score,
    higher = better), each selected model additionally costs lam*(1-q_m): coverage and
    per-model quality become competing terms in ONE objective, so quality influences
    every selection decision rather than only pre-filtering candidates (smaa_link=
    "prefilter", which hides candidates from the objective entirely) or arbitrating where
    coverage happens to be indifferent (smaa_link="tiebreak", which by construction never
    overrides a coverage preference). lam is the stated exchange rate between the two and
    is swept, not tuned — see the "pareto" mode in the main block.

    Returns (selected_models, n_covered, solver_status). budget=None means "cover
    everything reachable" (no cap), matching greedy's unlimited-budget default.
    """
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import lil_matrix

    restricted_covered = {m: (s & universe) for m, s in covered.items()}
    models = sorted(restricted_covered)
    targets = sorted(universe)
    n_m, n_t = len(models), len(targets)
    target_idx = {t: i for i, t in enumerate(targets)}
    eff_budget = n_m if budget is None else int(budget)

    n_vars = n_m + n_t
    c = np.zeros(n_vars)
    c[n_m:] = -1.0   # maximize sum(y) == minimize -sum(y)
    if lam and quality:
        # per-model penalty lam*(1-q_m); minimisation form, so it enters c positively
        for mi, m in enumerate(models):
            c[mi] = float(lam) * (1.0 - float(quality.get(m, 0.0)))

    A_cov = lil_matrix((n_t, n_vars))
    for t in targets:
        ti = target_idx[t]
        A_cov[ti, n_m + ti] = 1.0
        for mi, m in enumerate(models):
            if t in restricted_covered[m]:
                A_cov[ti, mi] = -1.0
    cov_constraint = LinearConstraint(A_cov.tocsr(), ub=np.zeros(n_t))

    A_budget = lil_matrix((1, n_vars))
    A_budget[0, :n_m] = 1.0
    budget_constraint = LinearConstraint(A_budget.tocsr(), ub=[eff_budget])

    result = milp(c=c, constraints=[cov_constraint, budget_constraint],
                  integrality=np.ones(n_vars), bounds=Bounds(0, 1))
    if not result.success:
        raise SystemExit(f"select_ensemble (ilp mode): MILP failed: {result.message}")

    x = result.x[:n_m]
    selected = sorted(m for m, xi in zip(models, x) if xi > 0.5)
    # count the y variables directly rather than reading -result.fun: with lam > 0 the
    # objective value also carries the quality penalty, so it is NOT the covered count
    n_covered = int(round(float(result.x[n_m:].sum())))
    return selected, n_covered, result.message


# ── main ───────────────────────────────────────────────────────────────────────

if enabled:
    prod = Path(products)

    present = list(model_keys or [])
    if not present:                     # fall back to the consensus panel
        try:
            _xm = upstream[consensus_upstream]
            summ = json.loads(Path(_xm["summary"]).read_text(encoding="utf-8"))
            present = (summ.get("summary") or {}).get("models_present") or []
        except Exception as ex:
            raise SystemExit(f"no model_keys and could not read consensus summary: {ex}")

    gt_counts, ke_titles = _load_gt(prod, present)
    # KEC universe: items tagged ("K", ke, ref_idx) so KEC and grounded targets can be
    # unioned into one opaque item space the greedy loop treats uniformly.
    kec_items = {("K", ke, r) for ke, n in gt_counts.items() for r in range(n)}
    if not kec_items:
        raise SystemExit("no ground-truth (structured) triples found — nothing to cover")
    total_gt = len(kec_items)

    # per-model coverage sets + pooled prediction counts (KEC coverage, tagged "K")
    covered, pred_stats, skipped = {}, {}, []
    for mk in present:
        got = _load_covered(prod, mk, gt_counts)
        if got is None:
            skipped.append(mk)
            continue
        cov, n_matched, n_pred = got
        covered[mk] = {("K", ke, r) for (ke, r) in cov}
        pred_stats[mk] = (n_matched, n_pred)
    if skipped:
        logger.warning(f"{len(skipped)} model(s) without kec_match_report.json — skipped: "
                       + ", ".join(skipped))
    if len(covered) < 1:
        raise SystemExit("no models with kec_match_report.json — nothing to select")

    # ── optional alternative target: ontology-grounded novel consensus triples ──
    # A model covers a grounded-novel target iff it is a member of that cluster. KEC
    # coverage is always reported separately, so target=="kec" stays behaviourally
    # unchanged (grounded_targets is empty; the union == the KEC universe).
    grounded_targets = {}
    if target == "kec_plus_grounded":
        levels = set(grounded_match_levels or [])
        grounding = _load_grounding(prod, present, levels)
        cons_path = upstream[consensus_upstream]["consensus"]
        grounded_targets = _load_grounded_targets(cons_path, grounding, int(grounded_min_support))
        for tid, mods in grounded_targets.items():
            for mk in mods:
                if mk in covered:
                    covered[mk].add(tid)
        logger.info(f"target=kec_plus_grounded: {len(grounded_targets)} grounded-novel "
                    f"targets (levels={sorted(levels)}, min_support={grounded_min_support})")

    universe = kec_items | set(grounded_targets)
    total_targets = len(universe)

    # single-best (max over individual models) — computed identically for every mode, so
    # it is a fair yardstick regardless of which selection mode produced `recommended`.
    # Fraction of `universe` (matches what greedy's own max_frac/steps curve measures —
    # KEC-only when target=="kec", KEC+grounded when target=="kec_plus_grounded).
    single_best_frac = (max(len(covered[m] & universe) for m in covered) / total_targets
                        if covered and total_targets else 0.0)

    # Per-model CURATED coverage, and the single-best over it. Reported separately from
    # single_best_frac because the two answer different questions and can rank models
    # differently: single_best_frac is against `universe` (which includes grounded-novel
    # candidates when target=="kec_plus_grounded"), whereas this is against the curated
    # reference alone. On the 116-KE pilot the leader differs between the two, so quoting
    # the joint figure as "curated coverage" understates the best curated recovery — the
    # manuscript needs both, and needs them regenerable rather than hand-computed.
    curated_by_model = {m: len(s & kec_items) for m, s in covered.items()}
    curated_rank = sorted(curated_by_model.items(), key=lambda kv: (-kv[1], kv[0]))
    single_best_curated_n = curated_rank[0][1] if curated_rank else 0
    single_best_curated_frac = (single_best_curated_n / total_gt) if total_gt else 0.0
    # every model tied at the top, since ties are common and the prose names them
    single_best_curated_models = [m for m, n in curated_rank if n == single_best_curated_n]
    per_model_curated = [
        {"rank": i, "model": m, "n_curated_covered": n,
         "curated_frac": round(n / total_gt, 4) if total_gt else 0.0}
        for i, (m, n) in enumerate(curated_rank, 1)
    ]
    logger.info(f"single best on curated reference: {single_best_curated_n}/{total_gt} "
                f"({single_best_curated_frac:.4f}) by {len(single_best_curated_models)} "
                f"model(s); single best on universe: {single_best_frac:.4f}")

    # emitted by every mode below
    curated_summary_fields = {
        "single_best_curated_n": single_best_curated_n,
        "single_best_curated_frac": round(single_best_curated_frac, 4),
        "single_best_curated_models": single_best_curated_models,
        "per_model_curated": per_model_curated,
    }

    if str(ensemble_mode).startswith("pareto"):
        # ── coverage vs. per-model quality, jointly optimised, swept over lam ───────
        # See the ensemble_mode param docstring for why neither smaa_link coupling is
        # sufficient and why quality belongs IN the objective.
        #
        # "pareto_topsis" and "pareto_smaa" (see PARETO_MODE_COLUMN) each pick their
        # quality column AND get their own product directory, so both can be run and
        # compared without overwriting each other. Bare "pareto" falls back to whatever
        # quality_column is set to (default topsis_score) and shares "pareto"'s path.
        if ensemble_mode in PARETO_MODE_COLUMN:
            quality_column = PARETO_MODE_COLUMN[ensemble_mode]
        try:
            _smaa_json = upstream[smaa_upstream]["json"]
        except Exception as ex:
            raise SystemExit(f"ensemble_mode={ensemble_mode!r} needs the smaa_rank "
                             f"upstream: {ex}")
        if quality_column not in QUALITY_DIRECTION:
            logger.warning(f"quality_column={quality_column!r} has no entry in "
                           f"QUALITY_DIRECTION — assuming higher-is-better")
        quality = _load_quality(_smaa_json, quality_column)
        if not quality:
            raise SystemExit(
                f"ensemble_mode={ensemble_mode!r} needs per-model quality from "
                f"smaa_criteria.tsv (column {quality_column!r}) — none loaded")
        missing_q = [m for m in covered if m not in quality]
        if missing_q:
            logger.warning(f"{len(missing_q)} candidate(s) absent from smaa_criteria.tsv "
                           f"— treated as q=0 (max penalty): " + ", ".join(sorted(missing_q)))

        frontier = []
        for lam in pareto_lambdas:
            sel, n_cov, status = _solve_ilp(covered, universe, budget, quality, float(lam))
            if not sel:
                logger.warning(f"lam={lam}: solver selected no models — skipped")
                continue
            cov_now = set().union(*(covered[m] for m in sel))
            qs = [quality.get(m, 0.0) for m in sel]
            frontier.append({
                "lam": float(lam),
                "n_models": len(sel),
                "n_covered": n_cov,
                "coverage_frac": round(n_cov / total_targets, 4) if total_targets else 0.0,
                "kec_frac": round(len(cov_now & kec_items) / total_gt, 4) if total_gt else 0.0,
                "mean_quality": round(sum(qs) / len(qs), 4),
                "min_quality": round(min(qs), 4),
                "models": sel,
                "solver_status": status,
            })
            logger.info(f"lam={lam:<6} models={len(sel):<3} coverage={n_cov}/{total_targets} "
                        f"({n_cov/total_targets:.4f}) mean_q={sum(qs)/len(qs):.4f}")
        if not frontier:
            raise SystemExit("pareto sweep produced no solutions")

        # Recommended = the knee: the frontier point maximising (coverage_frac +
        # mean_quality), i.e. the best balanced trade rather than either extreme. Reported
        # alongside the whole frontier, which is the actual deliverable.
        knee = max(frontier, key=lambda r: r["coverage_frac"] + r["mean_quality"])
        recommended = knee["models"]
        rec_set = set(recommended)
        rec_n = len(recommended)
        rec_covered_now = set().union(*(covered[m] for m in recommended))
        rec_kec = len(rec_covered_now & kec_items)
        rec_kec_frac = rec_kec / total_gt if total_gt else 0.0
        rec_grounded = len(rec_covered_now & set(grounded_targets))
        n_grounded_targets = len(grounded_targets)
        rec_grounded_frac = (rec_grounded / n_grounded_targets) if n_grounded_targets else 0.0
        rec_total_covered = knee["n_covered"]
        rec_frac = (rec_total_covered / total_targets) if total_targets else 0.0
        max_frac = (len(set().union(*covered.values()) & universe) / total_targets
                    if covered and total_targets else 0.0)
        solver_status = knee["solver_status"]
        logger.info(f"pareto knee: lam={knee['lam']} {rec_n} models, "
                    f"coverage={rec_frac:.4f}, mean_quality={knee['mean_quality']:.4f}")

        per_ke = {}
        for ke_id, n_gt in sorted(gt_counts.items()):
            covering = {}
            for r in range(n_gt):
                who = [m for m in recommended if ("K", ke_id, r) in covered[m]]
                if who:
                    covering[r] = who
            n_cov = len(covering)
            per_ke[ke_id] = {
                "title": ke_titles.get(ke_id, ""),
                "n_gt": n_gt,
                "n_covered": n_cov,
                "covered_frac": round(n_cov / n_gt, 4),
                "covering_models": {str(r): who for r, who in covering.items()},
                "missed_ref_idx": [r for r in range(n_gt) if r not in covering],
            }
        n_ke_covered = sum(1 for v in per_ke.values() if v["n_covered"] > 0)

        # per-model contribution within the recommended (knee) subset — same definitions
        # as ilp mode, plus the SMAA quality that earned each model its slot. All counts
        # are CURATED-TRIPLE counts except n_ke_touched; n_unique_triples counts only
        # triples no other selected model covers (what is lost if this model is dropped).
        per_model_contribution = {}
        for mk in recommended:
            mk_kec = covered[mk] & kec_items
            others = (set().union(*(covered[m] & kec_items
                                    for m in recommended if m != mk))
                      if rec_n > 1 else set())
            uniq = mk_kec - others
            n_matched, n_pred = pred_stats.get(mk, (0, 0))
            per_model_contribution[mk] = {
                "quality": round(quality.get(mk, 0.0), 4),
                "match_rate": round(n_matched / n_pred, 4) if n_pred else None,
                "n_pred_triples": n_pred,
                "n_triples_covered": len(mk_kec),
                "n_ke_touched": len({ke for (_k, ke, _r) in mk_kec}),
                "n_unique_triples": len(uniq),
                "unique_triples_ke": sorted({ke for (_k, ke, _r) in uniq}),
            }
        contribution_order = sorted(
            per_model_contribution,
            key=lambda m: (-per_model_contribution[m]["n_triples_covered"],
                           -per_model_contribution[m]["quality"]))

        # TSV = the frontier itself, one row per lam (the deliverable of this mode)
        OUT_TSV = Path(product["tsv"])
        OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_TSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["lam", "n_models", f"n_covered_of_{total_targets}", "coverage_frac",
                        "kec_frac", "mean_quality", "min_quality", "is_knee", "models"])
            for r in frontier:
                w.writerow([r["lam"], r["n_models"], r["n_covered"], r["coverage_frac"],
                            r["kec_frac"], r["mean_quality"], r["min_quality"],
                            "yes" if r is knee else "no", ";".join(r["models"])])

        # companion TSV: per-model breakdown of the recommended subset
        OUT_MODELS = OUT_TSV.parent / "ensemble_models.tsv"
        with open(OUT_MODELS, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["model", f"smaa_quality_{quality_column}", "match_rate",
                        "n_pred_triples", f"n_triples_covered_of_{total_gt}",
                        f"n_ke_touched_of_{len(gt_counts)}", "n_unique_triples",
                        "unique_triples_ke"])
            w.writerow(["ENSEMBLE_TOTAL (knee subset combined)", "", "", "",
                        rec_kec, n_ke_covered, "", ""])
            for mk in contribution_order:
                c = per_model_contribution[mk]
                w.writerow([mk, c["quality"], c["match_rate"], c["n_pred_triples"],
                            c["n_triples_covered"], c["n_ke_touched"],
                            c["n_unique_triples"], ";".join(c["unique_triples_ke"])])
        logger.info(f"wrote {OUT_MODELS.name}")

        summary = {
            "objective": "coverage_minus_lambda_quality_penalty",
            "target": target,
            "quality_column": quality_column,
            "pareto_lambdas": list(pareto_lambdas),
            "n_candidate_models": len(covered),
            "n_skipped_models": len(skipped),
            "total_gt_triples": total_gt,
            "n_gt_ke": len(gt_counts),
            "n_grounded_targets": n_grounded_targets,
            "total_target_items": total_targets,
            "params": {"budget": budget, "solver_status": solver_status},
            "grounded_params": {"grounded_match_levels": list(grounded_match_levels or []),
                                "grounded_min_support": grounded_min_support},
            "single_best_coverage_frac": round(single_best_frac, 4),
            **curated_summary_fields,
            "max_coverage_frac": round(max_frac, 4),
            "pareto_frontier": frontier,
            "knee_lam": knee["lam"],
            "knee_mean_quality": knee["mean_quality"],
            "recommended_models": recommended,
            "recommended_models_by_contribution": contribution_order,
            "per_model_contribution": per_model_contribution,
            "n_ke_covered": n_ke_covered,
            "recommended_coverage_frac": round(rec_frac, 4),
            "recommended_n_covered": rec_total_covered,
            "recommended_kec_frac": round(rec_kec_frac, 4),
            "recommended_grounded_frac": round(rec_grounded_frac, 4),
            "steps": [],
            "per_ke": per_ke,
        }
        Path(product["json"]).write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"wrote {OUT_TSV.name} and {Path(product['json']).name}")

        # figure: the frontier (coverage vs mean quality), knee marked
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.5, 5.0))
            xs = [r["mean_quality"] for r in frontier]
            ys = [r["coverage_frac"] * 100 for r in frontier]

            # ── panel A: the frontier, point size = ensemble size ──────────────
            ceiling = max_frac * 100
            ax.axhline(ceiling, ls=":", color="#888", lw=1.2, zorder=1)
            ax.annotate(f"panel ceiling {ceiling:.1f}%", (max(xs), ceiling),
                        textcoords="offset points", xytext=(-4, 5), fontsize=8,
                        color="#666", ha="right")
            ax.plot(xs, ys, "-", color="#2c7fb8", lw=1.6, zorder=2)
            ax.scatter(xs, ys, s=[18 + 9 * r["n_models"] for r in frontier],
                       c="#2c7fb8", zorder=3, edgecolor="white", linewidth=0.8)

            # free-lunch region: every point matching the ceiling coverage
            free = [r for r in frontier if r["coverage_frac"] * 100 >= ceiling - 1e-9]
            if len(free) > 1:
                best_free = max(free, key=lambda r: r["mean_quality"])
                ax.annotate(
                    f"same coverage, {frontier[0]['n_models']}→{best_free['n_models']} models",
                    (best_free["mean_quality"], best_free["coverage_frac"] * 100),
                    textcoords="offset points", xytext=(12, -26), fontsize=8.5,
                    color="#31a354", fontweight="bold",
                    arrowprops=dict(arrowstyle="->", color="#31a354", lw=1.2))

            # λ labels: alternate offsets so the crowded top-left stays readable
            for i, r in enumerate(frontier):
                dx, dy = (7, 7) if i % 2 == 0 else (7, -13)
                ax.annotate(f"λ={r['lam']:g}", (r["mean_quality"], r["coverage_frac"] * 100),
                            textcoords="offset points", xytext=(dx, dy), fontsize=7.5,
                            color="#444")
            ax.plot([knee["mean_quality"]], [knee["coverage_frac"] * 100], "o",
                    ms=15, mfc="none", mec="#d95f0e", mew=2.2, zorder=4,
                    label=f"balanced choice (λ={knee['lam']:g}, {knee['n_models']} models)")
            ax.set_xlabel(f"mean SMAA quality of selected models ({quality_column}, 0-1)")
            ax.set_ylabel("coverage of target (%)")
            ax.set_title("A. Coverage vs. panel quality: exact frontier\n"
                         "(marker size ∝ ensemble size)", fontsize=10.5)
            ax.legend(loc="lower left", fontsize=8)
            ax.grid(alpha=0.25)

            # ── panel B: what each frontier point costs, per λ ─────────────────
            idx = list(range(len(frontier)))
            ax2.plot(idx, [r["coverage_frac"] * 100 for r in frontier], "-o",
                     color="#2c7fb8", ms=4, lw=1.5, label="joint coverage")
            ax2.plot(idx, [r["kec_frac"] * 100 for r in frontier], "-^",
                     color="#31a354", ms=4, lw=1.5, label="curated (KEC) coverage")
            ax2.plot(idx, [r["mean_quality"] * 100 for r in frontier], "-s",
                     color="#d95f0e", ms=4, lw=1.5, label="mean SMAA quality (×100)")
            ax2.axvline(frontier.index(knee), ls="--", color="#d95f0e", lw=1.2, alpha=0.6)
            ax2.set_xticks(idx)
            ax2.set_xticklabels([f"{r['lam']:g}\n({r['n_models']})" for r in frontier],
                                fontsize=7.5)
            ax2.set_xlabel("λ (quality penalty weight)\n(models selected)")
            ax2.set_ylabel("percent")
            ax2.set_title("B. What quality costs in coverage", fontsize=10.5)
            ax2.legend(loc="lower left", fontsize=8)
            ax2.grid(alpha=0.25)

            fig.tight_layout()
            OUT_FIG = Path(product["fig"])
            OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(OUT_FIG, dpi=150, bbox_inches="tight")
            plt.close(fig)
            logger.info(f"wrote {OUT_FIG}")
        except Exception as ex:
            logger.warning(f"could not render pareto figure: {ex}")

    elif ensemble_mode == "ilp":
        # ── exact maximum-coverage mode — independent of greedy, own summary/figure ──
        # universe follows `target` exactly like greedy (kec_items alone, or kec_items |
        # grounded_targets when target=="kec_plus_grounded") — see _solve_ilp docstring.
        selected, n_covered, solver_status = _solve_ilp(covered, universe, budget)
        if not selected:
            raise SystemExit("select_ensemble (ilp mode): solver returned no models")
        recommended = selected
        rec_set = set(recommended)
        rec_n = len(recommended)
        rec_covered_now = set().union(*(covered[m] for m in recommended))
        rec_kec = len(rec_covered_now & kec_items)
        rec_kec_frac = rec_kec / total_gt if total_gt else 0.0
        rec_grounded = len(rec_covered_now & set(grounded_targets))
        n_grounded_targets = len(grounded_targets)
        rec_grounded_frac = (rec_grounded / n_grounded_targets) if n_grounded_targets else 0.0
        rec_total_covered = n_covered   # == rec_kec + rec_grounded, from the solver directly
        rec_frac = (rec_total_covered / total_targets) if total_targets else 0.0
        # fraction of universe reachable by ALL candidates combined (matches greedy's
        # max_frac == steps[-1][3] semantics — universe-relative, not KEC-only)
        max_frac = (len(set().union(*covered.values()) & universe) / total_targets
                   if covered and total_targets else 0.0)
        units = "target items" if grounded_targets else "GT triples"
        logger.info(f"ilp-exact over {len(covered)} models: best single = "
                    f"{single_best_frac:.3f}, exact optimum = {rec_n} models = "
                    f"{rec_frac:.3f} of {total_targets} {units} "
                    f"(KEC {rec_kec}/{total_gt}, grounded {rec_grounded}/{n_grounded_targets}) "
                    f"— solver: {solver_status}")

        per_ke = {}
        for ke_id, n_gt in sorted(gt_counts.items()):
            covering = {}
            for r in range(n_gt):
                who = [m for m in recommended if ("K", ke_id, r) in covered[m]]
                if who:
                    covering[r] = who
            n_cov = len(covering)
            per_ke[ke_id] = {
                "title": ke_titles.get(ke_id, ""),
                "n_gt": n_gt,
                "n_covered": n_cov,
                "covered_frac": round(n_cov / n_gt, 4),
                "covering_models": {str(r): who for r, who in covering.items()},
                "missed_ref_idx": [r for r in range(n_gt) if r not in covering],
            }

        # per-model contribution to the recommended subset — makes the solver's choice
        # legible instead of a black box. Deliberately KEC-only (curated ground truth)
        # regardless of `target`/universe, so this breakdown stays anchored to endorsed
        # triples even when the solver itself optimised over the larger KEC+grounded
        # universe — mirrors the module-wide convention that "KEC coverage is ALWAYS
        # reported separately" (see module docstring). Every count below is a TRIPLE count
        # unless labelled _ke: kec_items is the union of ALL (ke_id, ref_idx) GT triples
        # across ALL n_gt_ke KEs, so a model's n_triples_covered can exceed any one KE's
        # triple count many times over — it's summed across every KE the model touches
        # (most KEs have 1-4 GT triples, see per_ke's n_gt). n_unique_triples is
        # deliberately much smaller — it only counts triples NO OTHER recommended model
        # also covers (what would be LOST if this one model were cut), not overall
        # usefulness.
        per_model_contribution = {}
        for mk in recommended:
            mk_kec = covered[mk] & kec_items
            others_union = set().union(*(covered[m] & kec_items
                                         for m in recommended if m != mk)) if rec_n > 1 else set()
            unique = mk_kec - others_union
            mk_kes = {ke for (_k, ke, _r) in mk_kec}
            per_model_contribution[mk] = {
                "n_triples_covered": len(mk_kec),
                "n_ke_touched": len(mk_kes),
                "n_unique_triples": len(unique),
                "unique_triples_ke": sorted({ke for (_k, ke, _r) in unique}),
            }
        # sorted by unique contribution (most pivotal first), ties by total coverage
        contribution_order = sorted(
            per_model_contribution,
            key=lambda m: (-per_model_contribution[m]["n_unique_triples"],
                           -per_model_contribution[m]["n_triples_covered"]))

        # steps-shaped TSV (one row per recommended model, contribution order) so the
        # product is genuinely informative rather than a placeholder. A leading
        # ENSEMBLE_TOTAL row gives the combined-subset triple/KE totals so the file is
        # legible without cross-referencing ensemble_summary.json's recommended_kec_frac.
        OUT_TSV = Path(product["tsv"])
        OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
        n_ke_covered = sum(1 for v in per_ke.values() if v["n_covered"] > 0)
        with open(OUT_TSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["model", "in_recommended",
                        f"n_triples_covered_of_{total_gt}",
                        f"n_ke_touched_of_{len(gt_counts)}",
                        "n_unique_triples", "unique_triples_ke"])
            w.writerow(["ENSEMBLE_TOTAL (all recommended models combined)", "yes",
                        rec_kec, n_ke_covered, "", ""])
            for mk in contribution_order:
                c = per_model_contribution[mk]
                w.writerow([mk, "yes", c["n_triples_covered"], c["n_ke_touched"],
                            c["n_unique_triples"], ";".join(c["unique_triples_ke"])])
            for mk in sorted(set(covered) - rec_set):
                w.writerow([mk, "no", "", "", "", ""])

        summary = {
            "objective": "max_coverage_exact_ilp",
            "target": target,
            "n_candidate_models": len(covered),
            "n_skipped_models": len(skipped),
            "total_gt_triples": total_gt,
            "n_gt_ke": len(gt_counts),
            "n_grounded_targets": n_grounded_targets,
            "total_target_items": total_targets,
            "params": {"budget": budget, "solver_status": solver_status},
            "grounded_params": {"grounded_match_levels": list(grounded_match_levels or []),
                                "grounded_min_support": grounded_min_support},
            "single_best_coverage_frac": round(single_best_frac, 4),
            **curated_summary_fields,
            "max_coverage_frac": round(max_frac, 4),
            "recommended_models": recommended,
            # same models as recommended_models, ordered most-to-least PIVOTAL (by unique
            # triple contribution within the subset, ties by total coverage) — NOT by
            # overall usefulness. See per_model_contribution_note.
            "recommended_models_by_contribution": contribution_order,
            "n_ke_covered": n_ke_covered,
            "per_model_contribution_note": (
                "All counts here are TRIPLE counts (kec_items = the union of every KE's GT "
                "triples; most KEs have only 1-4 — see per_ke's n_gt), except n_ke_touched. "
                "n_triples_covered is each model's own total contribution (the headline "
                "number, out of total_gt_triples) — always much larger than "
                "n_unique_triples, which only counts triples NO OTHER recommended model "
                "also covers (what would be lost if this one model were cut), not that "
                "model's overall usefulness. recommended_kec_frac above is the COMBINED "
                f"ensemble total ({rec_kec}/{total_gt} triples, {n_ke_covered}/"
                f"{len(gt_counts)} KEs touched); summing n_unique_triples across models "
                "undercounts it because most coverage is redundant across models."),
            "per_model_contribution": per_model_contribution,
            "recommended_coverage_frac": round(rec_frac, 4),
            "recommended_n_covered": rec_total_covered,
            "recommended_kec_frac": round(rec_kec_frac, 4),
            "recommended_grounded_frac": round(rec_grounded_frac, 4),
            "steps": [],
            "per_ke": per_ke,
        }
        Path(product["json"]).write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"wrote {OUT_TSV.name} and {Path(product['json']).name}")

        # per-KE bar chart only (panel A needs greedy's step curve, which this mode lacks)
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax2 = plt.subplots(1, 1, figsize=(5, 4.8))
            fracs = [v["covered_frac"] for v in per_ke.values()]
            full = sum(1 for x in fracs if x >= 0.999)
            none_ = sum(1 for x in fracs if x <= 0.001)
            part = len(fracs) - full - none_
            ax2.bar(["fully\ncovered", "partially\ncovered", "not\ncovered"],
                    [full, part, none_], color=["#31a354", "#fec44f", "#de2d26"])
            for i, v in enumerate([full, part, none_]):
                ax2.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
            ax2.set_ylabel("number of KEs")
            ax2.set_title(f"Per-KE coverage (exact-optimum {rec_n}-model subset)")
            ax2.grid(alpha=0.25, axis="y")

            fig.tight_layout()
            OUT_FIG = Path(product["fig"])
            OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(OUT_FIG, dpi=150, bbox_inches="tight")
            plt.close(fig)
            logger.info(f"wrote {OUT_FIG}")
        except Exception as ex:
            logger.warning(f"could not render coverage figure: {ex}")

    else:
      # smaa_summary path comes from the smaa_rank upstream product (only needed when linked)
      smaa_summary = None
      if smaa_link in ("tiebreak", "prefilter"):
          try:
              smaa_summary = upstream[smaa_upstream]["json"]
          except Exception as ex:
              logger.warning(f"smaa_link={smaa_link} but no {smaa_upstream} upstream: {ex}")
      smaa_prob = _load_smaa_rank(smaa_summary) if smaa_link in ("tiebreak", "prefilter") else {}

      # candidate pool (optionally pre-filtered to the top-N SMAA models)
      candidates = set(covered)
      if smaa_link == "prefilter" and smaa_prob:
          top = sorted(smaa_prob, key=lambda m: -smaa_prob[m])[:int(smaa_topn)]
          candidates &= set(top)
          logger.info(f"prefilter: restricted to top-{smaa_topn} SMAA models "
                      f"({len(candidates)} of {len(covered)} have coverage data)")
          if not candidates:
              raise SystemExit("prefilter left no candidates — check smaa_topn / smaa_summary")

      # ── greedy maximum-coverage ────────────────────────────────────────────────
      # tie-break: higher SMAA prob (if smaa_link==tiebreak) then model name — deterministic
      def _tiebreak_key(m):
          return (-smaa_prob.get(m, 0.0), m) if smaa_link == "tiebreak" else (m,)

      uncovered = set(universe)
      remaining = set(candidates)
      # step: (model, new_items, cum_total, cum_frac, pooled_prec, cum_kec, cum_grounded)
      steps = []
      cum_matched = cum_pred = 0
      while remaining and uncovered:
          best = min(remaining,
                     key=lambda m: (-len(covered[m] & uncovered), *_tiebreak_key(m)))
          gain = len(covered[best] & uncovered)
          if gain == 0:
              break
          uncovered -= covered[best]
          remaining.discard(best)
          n_m, n_p = pred_stats[best]
          cum_matched += n_m
          cum_pred += n_p
          covered_now = universe - uncovered
          cum_total = len(covered_now)
          cum_kec = sum(1 for x in covered_now if x[0] == "K")
          cum_grounded = cum_total - cum_kec
          steps.append((best, gain, cum_total, cum_total / total_targets,
                        (cum_matched / cum_pred) if cum_pred else None,
                        cum_kec, cum_grounded))

      # recommended subset = leading prefix meeting min_gain (and budget cap)
      rec_n = 0
      for (_m, gain, *_rest) in steps:
          if gain < min_gain:
              break
          rec_n += 1
          if budget is not None and rec_n >= int(budget):
              break
      rec_n = max(rec_n, 1) if steps else 0          # always recommend ≥1 model if any
      recommended = [s[0] for s in steps[:rec_n]]
      rec_set = set(recommended)
      rec_frac = steps[rec_n - 1][3] if rec_n else 0.0

      max_frac = steps[-1][3] if steps else 0.0
      # KEC-only fractions for the recommended subset (always reported, both modes)
      rec_kec = steps[rec_n - 1][5] if rec_n else 0
      rec_grounded = steps[rec_n - 1][6] if rec_n else 0
      rec_kec_frac = rec_kec / total_gt if total_gt else 0.0
      n_grounded_targets = len(grounded_targets)
      rec_grounded_frac = (rec_grounded / n_grounded_targets) if n_grounded_targets else 0.0
      units = "target items" if grounded_targets else "GT triples"
      logger.info(f"greedy over {len(candidates)} models: best single = {single_best_frac:.3f}, "
                  f"recommended {rec_n} models = {rec_frac:.3f}, "
                  f"all models reach {max_frac:.3f} of {total_targets} {units} "
                  f"(KEC {rec_kec}/{total_gt}, grounded {rec_grounded}/{n_grounded_targets})")

      # ── per-KE coverage for the recommended subset (KEC only) ──────────────────
      per_ke = {}
      for ke_id, n_gt in sorted(gt_counts.items()):
          covering = {}
          for r in range(n_gt):
              who = [m for m in recommended if ("K", ke_id, r) in covered[m]]
              if who:
                  covering[r] = who
          n_cov = len(covering)
          per_ke[ke_id] = {
              "title": ke_titles.get(ke_id, ""),
              "n_gt": n_gt,
              "n_covered": n_cov,
              "covered_frac": round(n_cov / n_gt, 4),
              "covering_models": {str(r): who for r, who in covering.items()},
              "missed_ref_idx": [r for r in range(n_gt) if r not in covering],
          }

      # ── write greedy-steps TSV ─────────────────────────────────────────────────
      OUT_TSV = Path(product["tsv"])
      OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
      with open(OUT_TSV, "w", newline="", encoding="utf-8") as f:
          w = csv.writer(f, delimiter="\t")
          w.writerow(["step", "model", "new_gt", "cum_gt", "cum_coverage_frac",
                      "pooled_precision", "cum_kec", "cum_grounded", "in_recommended"])
          for i, (mk, gain, cum_gt, frac, prec, c_kec, c_gr) in enumerate(steps, 1):
              w.writerow([i, mk, gain, cum_gt, round(frac, 4),
                          "" if prec is None else round(prec, 4),
                          c_kec, c_gr,
                          "yes" if mk in rec_set else "no"])

      # ── write summary JSON ─────────────────────────────────────────────────────
      summary = {
          "objective": "max_coverage",
          "target": target,
          "n_candidate_models": len(candidates),
          "n_skipped_models": len(skipped),
          "total_gt_triples": total_gt,
          "n_gt_ke": len(gt_counts),
          "n_grounded_targets": n_grounded_targets,
          "total_target_items": total_targets,
          "params": {"min_gain": min_gain, "budget": budget,
                     "smaa_link": smaa_link, "smaa_topn": smaa_topn},
          "grounded_params": {"grounded_match_levels": list(grounded_match_levels or []),
                              "grounded_min_support": grounded_min_support},
          "single_best_coverage_frac": round(single_best_frac, 4),
          **curated_summary_fields,
          "max_coverage_frac": round(max_frac, 4),
          "recommended_models": recommended,
          "recommended_coverage_frac": round(rec_frac, 4),
          "recommended_n_covered": (rec_n and steps[rec_n - 1][2]) or 0,
          "recommended_kec_frac": round(rec_kec_frac, 4),
          "recommended_grounded_frac": round(rec_grounded_frac, 4),
          "steps": [
              {"step": i, "model": mk, "new_gt": gain, "cum_gt": cum_gt,
               "cum_coverage_frac": round(frac, 4),
               "pooled_precision": (None if prec is None else round(prec, 4)),
               "cum_kec": c_kec, "cum_grounded": c_gr,
               "in_recommended": mk in rec_set}
              for i, (mk, gain, cum_gt, frac, prec, c_kec, c_gr) in enumerate(steps, 1)
          ],
          "per_ke": per_ke,
      }
      Path(product["json"]).write_text(
          json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
      logger.info(f"wrote {OUT_TSV.name} and {Path(product['json']).name}")

      # ── coverage-curve figure ──────────────────────────────────────────────────
      try:
          import matplotlib
          matplotlib.use("Agg")
          import matplotlib.pyplot as plt

          xs = list(range(1, len(steps) + 1))
          ys = [s[3] * 100 for s in steps]

          fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12, 4.8),
                                        gridspec_kw={"width_ratios": [3, 2]})

          # panel A — cumulative coverage vs number of models (the star)
          main_label = ("union coverage (KEC + grounded)" if grounded_targets
                        else None)
          ax.plot(xs, ys, "-o", color="#2c7fb8", ms=4, lw=1.6, label=main_label)
          if grounded_targets and total_gt:
              # overlay the KEC-only cumulative curve so the endorsed share is legible
              ax.plot(xs, [s[5] / total_gt * 100 for s in steps], "-s",
                      color="#31a354", ms=3, lw=1.3, label="KEC-endorsed only")
          if rec_n:
              ax.axvline(rec_n, ls="--", color="#d95f0e", lw=1.4)
              ax.annotate(f"recommended: {rec_n} models\n{rec_frac*100:.1f}% coverage",
                          xy=(rec_n, rec_frac * 100),
                          xytext=(rec_n + max(1, len(xs) * 0.04), rec_frac * 100 - 8),
                          color="#d95f0e", fontsize=9,
                          arrowprops=dict(arrowstyle="->", color="#d95f0e"))
          ax.axhline(single_best_frac * 100, ls=":", color="#888", lw=1,
                     label=f"best single model: {single_best_frac*100:.1f}%")
          ax.set_xlabel("number of models (greedy order)")
          ax.set_ylabel("cumulative coverage (%)" if grounded_targets
                        else "cumulative GT coverage (%)")
          ax.set_ylim(0, 100)
          ax.set_title(
              (f"Ensemble coverage of {total_targets} targets "
               f"({total_gt} KEC + {n_grounded_targets} grounded-novel)")
              if grounded_targets else
              f"Ensemble coverage of {total_gt} KEC-endorsed triples")
          ax.legend(loc="lower right", fontsize=8)
          ax.grid(alpha=0.25)

          # panel B — per-KE coverage-fraction distribution for the recommended subset
          fracs = [v["covered_frac"] for v in per_ke.values()]
          full = sum(1 for x in fracs if x >= 0.999)
          none_ = sum(1 for x in fracs if x <= 0.001)
          part = len(fracs) - full - none_
          ax2.bar(["fully\ncovered", "partially\ncovered", "not\ncovered"],
                  [full, part, none_], color=["#31a354", "#fec44f", "#de2d26"])
          for i, v in enumerate([full, part, none_]):
              ax2.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
          ax2.set_ylabel("number of KEs")
          ax2.set_title(f"Per-KE coverage (recommended {rec_n}-model subset)")
          ax2.grid(alpha=0.25, axis="y")

          fig.tight_layout()
          OUT_FIG = Path(product["fig"])
          OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
          fig.savefig(OUT_FIG, dpi=150, bbox_inches="tight")
          plt.close(fig)
          logger.info(f"wrote {OUT_FIG}")
      except Exception as ex:
          logger.warning(f"could not render coverage figure: {ex}")
