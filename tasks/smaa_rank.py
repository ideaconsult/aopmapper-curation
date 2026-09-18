"""
smaa_rank.py — Ploomber task: rank the model panel with SMAA to pick the best
model(s) to commit to the full KE list.

Stochastic Multicriteria Acceptability Analysis (SMAA-2). The per-model quality
metrics come from a small pilot (the endorsed-KE subset), so the criteria values
carry sampling uncertainty; and we do not want to pre-commit to fixed criterion
weights. SMAA handles both: it samples criterion values from their uncertainty and
weight vectors uniformly from the simplex, and reports — for each model — the
probability of landing at each rank (rank-acceptability indices), the headline
first-rank probability, and the central weights that make a model win.

Criteria (all higher-is-better):
  match_rate          precision vs curated GT          (model_summary.csv)
  gt_coverage         recall of curated GT triples     (derived: kec_match_report)
  method_iri_frac     share of exact-IRI matches       (model_summary.csv)
  iri_valid_frac      share of proposed IRIs that resolve in OLS (validate_kec_iris;
                      opt in by listing it in `criteria`, off by default)
  logprob_best_auc    does confidence predict correctness (model_summary.csv; may be
                      missing for models run without logprobs — imputed)
  consensus_agreement fraction of triples agreeing with the panel (derived: consensus)

Optional, off by default (judge leniency on existing curation, not extraction quality):
  audit_correct_frac           self-judge audit  (model_summary.csv)
  audit_correct_frac_external  external-judge audit (products/judge/<model>/)

Ploomber:
  upstream: ["crossmodel_compare"]
  product:  {"tsv": rank-acceptability, "json": summary, "fig": stacked-bar png}
  params:   products, model_keys, criteria, n_iter, audit_*; or ranking_config +
            ranking to select a named block of the above (env.consensus.yaml,
            like aop_xml/version). Opt criteria in/out via the `criteria` list.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

# ── Ploomber task header ──────────────────────────────────────────────────────
# Preference weights over criteria (higher = more important). Used by TOPSIS instead of
# entropy weights, and — if weight_concentration > 0 — to centre SMAA's weight sampling.
# e.g. {"match_rate": 0.3, "gt_coverage": 0.3, "consensus_agreement": 0.25,
#       "action_margin_auc": 0.1, "method_iri_frac": 0.05}. None → entropy (TOPSIS) +
# uniform-simplex sampling (SMAA), the weight-agnostic default.
# Reuse for other tracks (e.g. assays): a criterion present as a plain column in
# the per-model summary CSV is read directly; only KEC-specific criteria are derived.
# Named ranking config (mirror of aop_xml/version): `ranking_config` is a dict of
# {name: {param: value, …}} blocks defined in env.consensus.yaml, and `ranking`
# picks which block to apply over the task defaults above. None → defaults as-is.
# + tags=["parameters"]
upstream = ["crossmodel_compare"]
product = None
enabled = True
products = "products_202606"
model_keys = None                       # None → use consensus models_present
criteria = [                            # default on-criteria (all higher-better)
    "match_rate",
    "gt_coverage",
    "method_iri_frac",
    "action_margin_auc",                # sign-checked calibration (principled);
                                        # use "logprob_best_auc" for the old max-AUC
    "consensus_agreement",
]
audit_as_criterion = False              # add self-judge audit_correct_frac
audit_external_as_criterion = False     # add external-judge audit_correct_frac
summary_filename = "model_summary.csv"
denominator_col = "n_pred_triples"      # binomial-SE denominator for plain columns
require_criterion = "match_rate"        # drop models lacking this; None → any criterion
consensus_upstream = "crossmodel_compare"   # upstream task key holding consensus+summary
criteria_weights = None
weight_concentration = 0.0    # SMAA: 0 = uniform (ignore prefs); larger = tighter around
                              # criteria_weights (try ~k…10k; mean stays at the prefs)
n_iter = 10000
seed = 0
ranking_config = None
ranking = "default"
topn_models = 15
# -

try:
    from tasks.logutil import setup_logging
    logger = setup_logging(Path(product["json"]).parent / "smaa_rank.log")
except Exception:                       # standalone / import-safe fallback
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("smaa_rank")


# ── apply the selected named ranking config ────────────────────────────────────
# Same idea as aop_xml[version]: pull one named block out of `ranking_config` and
# let its keys override the parameters above, so a single pipeline entry can drive
# several ranking variants (default / no_logprob / iri_valid / …) by name alone. Only
# keys that are already task parameters are honoured (typos are warned, not applied).
if ranking_config and ranking:
    _cfg = ranking_config.get(str(ranking))
    if _cfg is None:
        raise SystemExit(f"ranking '{ranking}' not in ranking_config "
                         f"(have: {sorted(ranking_config)})")
    _known = set(globals())
    for _k, _v in _cfg.items():
        if _k not in _known:
            logger.warning(f"ranking '{ranking}': unknown parameter '{_k}' — ignored")
            continue
        globals()[_k] = _v
    logger.info(f"applied ranking config '{ranking}': "
                + ", ".join(f"{k}={_cfg[k]}" for k in _cfg if k in _known))


# ── data loading & criterion derivation ───────────────────────────────────────

def _read_model_summary(prod: Path, mk: str, fname: str = "model_summary.csv") -> dict:
    path = prod / mk / fname
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[0] if rows else {}


def _f(v):
    """parse a CSV cell to float or None."""
    if v is None or v == "" or str(v).lower() in ("none", "nan", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _total_structured(prod: Path, present: list[str]) -> int:
    """Total curated GT triples (gt_coverage denominator). Same GT for all models."""
    for mk in present:
        p = prod / mk / "kec.json"
        if p.exists():
            kec = json.loads(p.read_text(encoding="utf-8"))
            return sum(len(r.get("structured") or []) for r in kec.values())
    return 0


def _gt_coverage(prod: Path, mk: str, total_struct: int) -> tuple[float | None, int]:
    """Recall: distinct curated GT triples recovered ÷ total curated triples."""
    path = prod / mk / "kec_match_report.json"
    if not path.exists() or not total_struct:
        return None, total_struct
    rep = json.loads(path.read_text(encoding="utf-8"))
    covered = 0
    for _ke, matches in (rep.get("per_ke") or {}).items():
        refs = {m["ref_idx"] for m in matches
                if m.get("matched") and m.get("ref_idx") is not None}
        covered += len(refs)
    return covered / total_struct, total_struct


def _consensus_agreement(consensus: dict, summ: dict) -> dict:
    """Per model: members in support≥2 clusters ÷ the model's total triples/assays.

    If the crossmodel summary already carries a pre-computed consensus_agreement
    (as crossmodel_assays.py does), use that directly rather than recomputing.
    """
    from collections import Counter
    in_consensus = Counter()
    for _ke, clusters in consensus.items():
        for cl in clusters:
            if cl.get("support", 0) >= 2:
                for m in cl.get("members", []):
                    in_consensus[m.get("model")] += 1
    per_model = (summ.get("summary") or {}).get("per_model") or {}
    out = {}
    for mk, rec in per_model.items():
        # assay summary pre-computes this; use it when available
        pre = rec.get("consensus_agreement")
        if pre is not None:
            out[mk] = pre
            continue
        # KEC path: divide cluster membership count by total LLM triples
        n = rec.get("n_llm_triples") or rec.get("n_assays") or 0
        out[mk] = (in_consensus[mk] / n) if n else None
    return out


def _external_audit_correct(prod: Path, mk: str) -> float | None:
    path = prod / "judge" / mk / "kec_truth_audit.json"
    if not path.exists():
        return None
    a = json.loads(path.read_text(encoding="utf-8"))
    s = a.get("summary", a)
    frac = s.get("triple_verdict_fraction") or s.get("verdict_fraction") or {}
    return frac.get("correct")


def _action_margin_auc(prod: Path, mk: str) -> float | None:
    """Sign-checked calibration: AUC of the action-margin feature predicting
    correctness, but ONLY if it points the right way (direction +1, i.e. a more
    decisive action token => more likely correct). A feature that separates by
    chance in the wrong direction (e.g. response_mean with direction -1) is NOT a
    usable trust gate, so it is clamped to 0.5 (no usable calibration).

    Unlike `logprob_best_auc` (max over all features, signs flipped to look
    favourable), this measures whether the *principled* confidence signal the
    workflow deploys is genuinely and interpretably calibrated.
    """
    path = prod / mk / "kec_logprob_calibration.json"
    if not path.exists():
        return None
    c = json.loads(path.read_text(encoding="utf-8"))
    s = c if "per_feature" in c else c.get("summary", {})
    pf = (s.get("per_feature") or {}).get("action_margin") or {}
    if "auc" not in pf:
        return None                       # no logprobs / insufficient data
    if pf.get("direction", 1) < 0:
        return 0.5                         # anti-predictive => not a usable gate
    return float(pf["auc"])


def _iri_valid_frac(prod: Path, mk: str, weighted: bool = True) -> tuple[float | None, int]:
    """Grounding quality: fraction of the model's proposed ontology IRIs that
    resolve in OLS (from validate_kec_iris.tsv).

    Only rows with a resolvable prefix and a decided status are scored: NULL /
    UNKNOWN / WIKI_ACTION carry no real identifier and rows still errored or
    unchecked are excluded, so this measures "of the IRIs the model committed to,
    how many exist". Occurrence-weighted by ke_count when weighted=True. Returns
    (frac, denominator) for the binomial SE. Meaningful only once validate_kec_iris
    has been re-run with the triple-aware collect_iris (else it reflects the
    organ/cell terms only, not the GO/CHEBI/PR object/process IRIs).
    """
    path = prod / mk / "validate_kec_iris.tsv"
    if not path.exists():
        return None, 0
    skip = {"NULL", "UNKNOWN", "WIKI_ACTION", "WIKI", ""}
    num = den = 0
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if (row.get("prefix") or "").upper() in skip:
                continue
            status = (row.get("ols_status") or "").strip()
            if status not in ("valid", "not_found"):
                continue
            try:
                w = int(row.get("ke_count") or 1) if weighted else 1
            except (TypeError, ValueError):
                w = 1
            den += w
            if status == "valid":
                num += w
    if den == 0:
        return None, 0
    return num / den, den


# ── SMAA-2 core ────────────────────────────────────────────────────────────────

def _binom_se(p: float | None, n: float | None, default: float = 0.05) -> float:
    if p is None or n in (None, 0):
        return default
    p = min(max(p, 0.0), 1.0)
    return max(np.sqrt(p * (1 - p) / n), 1e-4)


def smaa(values: np.ndarray, ses: np.ndarray, n_iter: int, rng,
         alpha: np.ndarray | None = None) -> dict:
    """values, ses: (n_models, n_criteria), already higher-is-better.

    Returns rank-acceptability matrix, first-rank prob, and central weights.
    Each iteration: perturb values by their SE, draw a weight vector, min-max
    normalise each criterion across models, score, rank.

    `alpha` is the Dirichlet concentration vector for the weight draw. None ⇒
    ones(k) ⇒ uniform on the simplex (no preference). To sample *around* preferred
    weights w_pref, pass alpha = kappa · w_pref: the mean draw equals w_pref and
    larger kappa concentrates the draws more tightly around it.
    """
    m, k = values.shape
    if alpha is None:
        alpha = np.ones(k)
    rank_counts = np.zeros((m, m), dtype=float)        # [model, rank]
    cw_sum = np.zeros((m, k), dtype=float)             # central-weight accumulator
    cw_n = np.zeros(m, dtype=float)

    for _ in range(n_iter):
        sample = rng.normal(values, ses)
        # min-max per criterion across models (constant column → 0.5)
        lo = sample.min(axis=0)
        hi = sample.max(axis=0)
        rng_span = np.where(hi > lo, hi - lo, 1.0)
        norm = np.where(hi > lo, (sample - lo) / rng_span, 0.5)
        w = rng.dirichlet(alpha)                       # uniform, or centred on prefs
        utility = norm @ w
        order = np.argsort(-utility)                   # best → worst
        for rank, mi in enumerate(order):
            rank_counts[mi, rank] += 1
        winner = order[0]
        cw_sum[winner] += w
        cw_n[winner] += 1

    rank_accept = rank_counts / n_iter
    central_w = np.divide(cw_sum, cw_n[:, None],
                          out=np.full_like(cw_sum, np.nan), where=cw_n[:, None] > 0)
    return {"rank_accept": rank_accept, "central_w": central_w,
            "first_rank": rank_accept[:, 0]}


# ── deterministic alternative: entropy-weighted TOPSIS ──────────────────────────
# A closed-form, reproducible cross-check for the (Monte-Carlo) SMAA ranking. The
# weights are data-driven (Shannon entropy: criteria that spread the models more get
# more weight), so no weights are hand-picked; TOPSIS then scores each model by its
# relative closeness to the ideal vs anti-ideal point. All criteria are higher-better.

def entropy_weights(V: np.ndarray) -> np.ndarray:
    """Shannon-entropy criterion weights for a (models × criteria) benefit matrix."""
    V = np.asarray(V, dtype=float)
    m, k = V.shape
    col_sum = V.sum(axis=0)
    P = np.divide(V, col_sum, out=np.zeros_like(V), where=col_sum > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        plnp = np.where(P > 0, P * np.log(P), 0.0)
    e = -plnp.sum(axis=0) / np.log(m) if m > 1 else np.zeros(k)
    d = 1.0 - e                                  # divergence = information content
    return (d / d.sum()) if d.sum() > 0 else np.full(k, 1.0 / k)


def topsis(V: np.ndarray, w: np.ndarray) -> np.ndarray:
    """TOPSIS closeness coefficient C∈[0,1] per model (all criteria higher-better)."""
    V = np.asarray(V, dtype=float)
    norm = np.sqrt((V ** 2).sum(axis=0))
    R = np.divide(V, norm, out=np.zeros_like(V), where=norm > 0)
    Vw = R * w
    a_best, a_worst = Vw.max(axis=0), Vw.min(axis=0)
    d_best = np.sqrt(((Vw - a_best) ** 2).sum(axis=1))
    d_worst = np.sqrt(((Vw - a_worst) ** 2).sum(axis=1))
    denom = d_best + d_worst
    return np.divide(d_worst, denom, out=np.full(len(V), 0.5), where=denom > 0)


# ── main ───────────────────────────────────────────────────────────────────────

if enabled:
    prod = Path(products)
    _xm = upstream[consensus_upstream]
    consensus = json.loads(Path(_xm["consensus"]).read_text(encoding="utf-8"))
    summ = json.loads(Path(_xm["summary"]).read_text(encoding="utf-8"))

    present = model_keys or (summ.get("summary") or {}).get("models_present") or []
    total_struct = _total_structured(prod, present)
    cons_agree = _consensus_agreement(consensus, summ)

    crit = list(criteria)
    if audit_as_criterion and "audit_correct_frac" not in crit:
        crit.append("audit_correct_frac")
    if audit_external_as_criterion and "audit_correct_frac_external" not in crit:
        crit.append("audit_correct_frac_external")

    # ── assemble per-model criterion values + denominators (for SE) ───────────
    recs = {}
    for mk in present:
        ms = _read_model_summary(prod, mk, summary_filename)
        n_pred = _f(ms.get("n_pred_triples"))
        gtc, _ = _gt_coverage(prod, mk, total_struct)
        n_audit = _f(ms.get("n_ke"))  # audit denominator ~ curated triples; KE count proxy
        # KEC-specific (derived). Other tracks supply criteria as plain columns below.
        vals = {
            "match_rate":          (_f(ms.get("match_rate")), n_pred),
            "gt_coverage":         (gtc, total_struct),
            "method_iri_frac":     (_f(ms.get("method_iri_frac")), n_pred),
            "action_margin_auc":   (_action_margin_auc(prod, mk), None),
            "iri_valid_frac":      _iri_valid_frac(prod, mk),
            "logprob_best_auc":    (_f(ms.get("logprob_best_auc")), None),
            "consensus_agreement": (cons_agree.get(mk),
                                    ((summ.get("summary") or {}).get("per_model") or {})
                                    .get(mk, {}).get("n_llm_triples")),
            "audit_correct_frac":  (_f(ms.get("audit_correct_frac")), n_audit),
            "audit_correct_frac_external": (_external_audit_correct(prod, mk), n_audit),
        }
        # Generic: a requested criterion present as a plain column in the summary CSV
        # is read directly (used by the assay track; KEC summaries lack these columns).
        den = (_f(ms.get(denominator_col)) or _f(ms.get("n_assays"))
               or _f(ms.get("n_pred_triples")))
        for c in crit:
            if c not in vals:
                vals[c] = (_f(ms.get(c)), den)        # plain column (or None)
            elif vals[c][0] is None:
                cv = _f(ms.get(c))
                if cv is not None:
                    vals[c] = (cv, den)               # column overrides empty derivation
        recs[mk] = vals

    # keep only models that have data (require_criterion, or any criterion if None)
    if require_criterion:
        models = [mk for mk in present
                  if recs[mk].get(require_criterion, (None,))[0] is not None]
    else:
        models = [mk for mk in present
                  if any(recs[mk].get(c, (None,))[0] is not None for c in crit)]
    if len(models) < 2:
        raise SystemExit(f"SMAA needs ≥2 models with metrics; got {len(models)}")

    # ── handle missing values per criterion (impute median; drop if all missing) ──
    used_crit, imputed = [], {}
    for c in crit:
        col = [recs[mk][c][0] for mk in models]
        n_missing = sum(v is None for v in col)
        if n_missing == len(col):
            logger.warning(f"criterion {c}: all missing — dropped")
            continue
        used_crit.append(c)
        if n_missing:
            med = float(np.median([v for v in col if v is not None]))
            imputed[c] = {"n_missing": n_missing, "value": round(med, 4)}
    if not used_crit:
        raise SystemExit("No usable criteria")

    # ── build value/SE matrices (all criteria here are higher-is-better) ──────
    V = np.zeros((len(models), len(used_crit)))
    S = np.zeros_like(V)
    for j, c in enumerate(used_crit):
        col = [recs[mk][c][0] for mk in models]
        med = float(np.median([v for v in col if v is not None])) if any(
            v is not None for v in col) else 0.0
        for i, mk in enumerate(models):
            v, n = recs[mk][c]
            if v is None:
                v = med
            V[i, j] = v
            S[i, j] = _binom_se(v, n)

    # ── preference weights (optional) → TOPSIS weights + SMAA sampling centre ──
    pref_w = None
    if criteria_weights:
        raw = np.array([float(criteria_weights.get(c, 0.0)) for c in used_crit])
        if raw.sum() > 0:
            pref_w = raw / raw.sum()
            logger.info("preference weights: "
                        + ", ".join(f"{c}={pref_w[j]:.3f}"
                                    for j, c in enumerate(used_crit)))
        else:
            logger.warning("criteria_weights sum to 0 over used criteria — ignored")

    # SMAA: uniform simplex unless a preference + concentration are given, in which
    # case sample a Dirichlet centred on pref_w (mean = pref_w; kappa = tightness).
    smaa_alpha = None
    if pref_w is not None and weight_concentration and weight_concentration > 0:
        smaa_alpha = float(weight_concentration) * np.clip(pref_w, 1e-6, None)
        logger.info(f"SMAA sampling centred on preference weights "
                    f"(concentration kappa={weight_concentration})")

    rng = np.random.default_rng(seed)
    res = smaa(V, S, n_iter, rng, alpha=smaa_alpha)

    order = np.argsort(-res["first_rank"])
    ranked = [models[i] for i in order]

    # ── write rank-acceptability TSV ──────────────────────────────────────────
    OUT_TSV = Path(product["tsv"])
    OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_TSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["model", "first_rank_prob",
                    *[f"rank_{r+1}" for r in range(len(models))]])
        for i in order:
            w.writerow([models[i], round(res["first_rank"][i], 4),
                        *[round(res["rank_accept"][i, r], 4)
                          for r in range(len(models))]])

    # ── write summary JSON ────────────────────────────────────────────────────
    summary = {
        "n_models": len(models),
        "n_iter": n_iter,
        "criteria": used_crit,
        "criteria_direction": {c: "higher_better" for c in used_crit},
        "imputed": imputed,
        "total_structured_gt": total_struct,
        "ranking": [
            {"model": models[i],
             "first_rank_prob": round(float(res["first_rank"][i]), 4),
             "central_weights": {
                 c: (round(float(res["central_w"][i, j]), 4)
                     if not np.isnan(res["central_w"][i, j]) else None)
                 for j, c in enumerate(used_crit)},
             "criteria_values": {
                 c: (round(float(V[i, j]), 4)) for j, c in enumerate(used_crit)}}
            for i in order
        ],
    }
    Path(product["json"]).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── deterministic cross-check: TOPSIS over the same matrix ────────────────
    # Weights: explicit preference weights if given (reflect what the curator values),
    # else Shannon-entropy weights (data-driven, but reward variance not importance).
    if pref_w is not None:
        topsis_w, weight_kind = pref_w, "preference"
    else:
        topsis_w, weight_kind = entropy_weights(V), "entropy"
    topsis_c = topsis(V, topsis_w)
    topsis_order = list(np.argsort(-topsis_c))            # best → worst
    topsis_rank = {int(mi): r + 1 for r, mi in enumerate(topsis_order)}
    logger.info(f"TOPSIS {weight_kind} weights: "
                + ", ".join(f"{c}={topsis_w[j]:.3f}"
                            for j, c in enumerate(used_crit)))

    # ── paper-ready criteria matrix (TSV + Markdown) ──────────────────────────
    # One row per model: SMAA first-rank probability, expected rank (the more honest
    # tail statistic), the deterministic entropy-TOPSIS score+rank, every criterion
    # value, and the SMAA central weights. Regenerated on every run (KEC and assay).
    exp_rank = {i: float(sum((r + 1) * res["rank_accept"][i, r]
                             for r in range(len(models)))) for i in range(len(models))}
    out_dir = Path(product["json"]).parent
    crit_cols = list(used_crit)
    headers = (["model", "first_rank_prob", "expected_rank",
                "topsis_score", "topsis_rank"]
               + crit_cols + [f"cw_{c}" for c in crit_cols])

    def _cw(i, j):
        v = res["central_w"][i, j]
        return "" if np.isnan(v) else round(float(v), 4)

    with open(out_dir / "smaa_criteria.tsv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(headers)
        for i in order:
            w.writerow([models[i], round(float(res["first_rank"][i]), 4),
                        round(exp_rank[i], 2),
                        round(float(topsis_c[i]), 4), topsis_rank[i],
                        *[round(float(V[i, j]), 4) for j in range(len(crit_cols))],
                        *[_cw(i, j) for j in range(len(crit_cols))]])

    # Markdown version for direct inclusion in the manuscript / discussion docs.
    def _short(mk):
        return mk.split("--", 1)[-1] if "--" in mk else mk
    md = ["| model | P(rank 1) | E[rank] | TOPSIS | "
          + " | ".join(crit_cols) + " |",
          "|---|---|---|---|" + "---|" * len(crit_cols)]
    for i in order:
        vals = " | ".join(f"{V[i, j]:.3f}" for j in range(len(crit_cols)))
        md.append(f"| {_short(models[i])} | {res['first_rank'][i]:.3f} | "
                  f"{exp_rank[i]:.1f} | {topsis_c[i]:.3f} | {vals} |")
    (out_dir / "smaa_criteria.md").write_text(
        f"# SMAA criteria matrix ({len(models)} models, {n_iter:,} iterations)\n\n"
        f"Criteria (all higher-is-better): {', '.join(crit_cols)}. "
        f"P(rank 1) = SMAA first-rank acceptability; E[rank] = SMAA expected rank "
        f"(1 = best, {len(models)} = worst); TOPSIS = deterministic {weight_kind}-weighted "
        f"closeness (∈[0,1], higher = better). TOPSIS {weight_kind} weights: "
        + ", ".join(f"{c}={topsis_w[j]:.3f}" for j, c in enumerate(used_crit))
        + ". Central weights are in smaa_criteria.tsv.\n\n"
        + "\n".join(md) + "\n", encoding="utf-8")
    logger.info(f"wrote criteria matrix → {out_dir/'smaa_criteria.tsv'} (+ .md)")

    # ── stacked-bar rank-acceptability figure ─────────────────────────────────
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import cm
        from tasks.figviz import model_colors, numbered_legend

        mcolors = model_colors(ranked)
        n = len(ranked)
        nums = [str(i + 1) for i in range(n)]        # "1", "2", ...
        ra = res["rank_accept"][order]                 # shape (n, n), display order

        legend_w = 3.2                                 # inches for right legend panel
        fig_h = max(4.5, n * 0.30 + 1.5)
        fig_w = max(6.0, 4.5) + legend_w
        fig, (ax, ax_leg) = plt.subplots(
            1, 2, figsize=(fig_w, fig_h),
            gridspec_kw={"width_ratios": [fig_w - legend_w, legend_w]},
        )

        rank_colors = cm.viridis_r(np.linspace(0, 1, n))
        lefts = np.zeros(n)
        bars = []
        for r in range(n):
            b = ax.barh(nums, ra[:, r], left=lefts, color=rank_colors[r],
                        label=f"rank {r + 1}" if r < 6 else None, height=0.7)
            bars.append(b)
            lefts += ra[:, r]

        # colour each bar's outer edge with the model's palette colour
        for bi, mk in enumerate(ranked):
            mc = mcolors.get(mk, "#888")
            for r_idx in range(n):
                bars[r_idx][bi].set_edgecolor(mc)
                bars[r_idx][bi].set_linewidth(1.5)

        ax.invert_yaxis()                              # best model at top
        ax.set_xlabel("rank-acceptability  P(rank)")
        ax.set_title(f"SMAA-2 model ranking  ({len(used_crit)} criteria,\n"
                     f"{n_iter:,} iterations)", fontsize=9)
        ax.set_xlim(0, 1)
        ax.set_ylabel("model (best at top)")

        # right panel: model names (top) + rank-layer colour key (bottom)
        from matplotlib.patches import Patch as _Patch
        ax_leg.axis("off")
        rank_handles = [_Patch(facecolor=rank_colors[r], label=f"rank {r + 1}")
                        for r in range(min(6, n))]
        rank_leg = ax_leg.legend(handles=rank_handles, ncol=1, loc="lower left",
                                 fontsize=9, title="rank layer", title_fontsize=9,
                                 frameon=True, handlelength=1.0, handleheight=1.0,
                                 labelspacing=0.3)
        ax_leg.add_artist(rank_leg)        # keep it when numbered_legend replaces the active legend
        numbered_legend(ax_leg, ranked, mcolors, ncol=1,
                        bbox=(0.0, 1.0), loc="upper left",
                        title="models")

        fig.tight_layout()
        Path(product["fig"]).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(product["fig"], dpi=130, bbox_inches="tight")
        plt.close(fig)
    except Exception as e:
        logger.warning(f"figure skipped: {e}")

    # ── parallel-coordinates over the SMAA criteria (replaces the crowded radar) ─
    # Shows ALL the criteria SMAA actually uses (not a separate hand-picked subset),
    # scales to many models far better than a radar, and highlights the leaders.
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import cm
        from tasks.figviz import model_colors, numbered_legend

        k = len(used_crit)
        xs = np.arange(k)
        # min-max normalise each criterion to [0,1] for a common vertical scale
        lo, hi = V.min(axis=0), V.max(axis=0)
        span = np.where(hi > lo, hi - lo, 1.0)
        Vn = np.where(hi > lo, (V - lo) / span, 0.5)

        n_top = min(topn_models, len(models))
        top = list(order[:n_top])                    # highlight the top-10 SMAA leaders
        tcolors = model_colors([models[i] for i in top])
        fig, ax = plt.subplots(figsize=(max(7, 1.6 * k + 2), 5.5))
        # background: every model as a faint grey line
        for i in range(len(models)):
            ax.plot(xs, Vn[i], color="#cccccc", lw=0.8, alpha=0.6, zorder=1)
        # foreground: the leaders, coloured + labelled
        for i in top:
            ax.plot(xs, Vn[i], color=tcolors.get(models[i]), lw=2.2,
                    marker="o", ms=4, zorder=3)
        ax.set_xticks(xs)
        ax.set_xticklabels(used_crit, rotation=20, ha="right", fontsize=8)
        ax.set_ylabel("criterion value (min–max normalised across models)")
        ax.set_ylim(-0.14, 1.10)
        # annotate actual min / max for each criterion at the bottom / top of its axis
        for j in range(k):
            ax.text(xs[j], -0.03, f"{lo[j]:.3g}", ha="center", va="top",
                    fontsize=6, color="#555555")
            ax.text(xs[j], 1.03, f"{hi[j]:.3g}", ha="center", va="bottom",
                    fontsize=6, color="#555555")
        ax.set_title(f"Model profiles across the {k} SMAA criteria "
                     f"(grey = all {len(models)} models; coloured = top {n_top})")
        numbered_legend(ax, [models[i] for i in top], tcolors, ncol=1,
                        bbox=(1.01, 1.0), loc="upper left", title=f"top {n_top} (SMAA)")
        fig.text(0.5, -0.02,
                 "Parallel-coordinates view of the exact criteria fed to SMAA, each "
                 "normalised 0–1 across models (actual min/max shown at axis ends). "
                 f"Every model is a line; the {n_top} highest "
                 "first-rank-probability models are highlighted. Crossing lines between "
                 "two axes indicate a trade-off (a model strong on one criterion, weak "
                 "on the other).", ha="center", va="top", fontsize=7.5,
                 style="italic", wrap=True)
        par_out = Path(product["fig"]).with_name("fig_smaa_parallel.png")
        fig.savefig(par_out, dpi=130, bbox_inches="tight")
        plt.close(fig)
        logger.info(f"wrote {par_out}")
    except Exception as e:
        logger.warning(f"parallel figure skipped: {e}")

    logger.info(f"SMAA over {len(models)} models, criteria={used_crit}")
    for i in order[:5]:
        logger.info(f"  {models[i]:<45} P(rank1)={res['first_rank'][i]:.3f}")
else:
    logger.info("smaa_rank disabled — skipping")
