"""
figures_crossmodel.py — Ploomber task: comparison figures (PNG) for the KEC workflow.

Generates the *data* comparison figures (not the architecture schematic — that is drawn
later by hand) from the per-model and cross-model product files. Everything is generic
over the configured `model_keys`, so re-running after more models are added simply
redraws richer figures. Robust to missing per-model artefacts (a model without an audit
or calibration file is skipped in the relevant panel, with a note in the log).

Figures written to `{products}/crossmodel/figures/`:
  - fig_match_rate.png        per-model match rate vs curated ground truth
  - fig_consensus_support.png distribution of cross-model agreement (support counts)
  - fig_pairwise_agreement.png model × model Jaccard heatmap
  - fig_audit_verdicts.png    per-model curated-triple verdicts (correct/partial/wrong)
  - fig_logprob_auc.png       per-model log-prob feature discrimination (AUC)
  - fig_logprob_roc.png       ROC of the action-margin trust signal, overlaid per model

Ploomber:
  upstream: ["crossmodel_compare"]
  product:  {"nb": ..., "dir": figures directory}
  params:   products, model_keys
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402
import numpy as np                # noqa: E402

from tasks.figviz import model_colors, numbered_legend, pairwise_heatmap  # noqa: E402
from tasks.logutil import setup_logging   # noqa: E402

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["crossmodel_compare"]
product = None
enabled = True
products = None
model_keys = None
show_captions = True   # set False to omit inline captions (e.g. for paper figures)
topn_models = 15
# -

FIG_DIR = Path(product["dir"])
logger = setup_logging(FIG_DIR / "figures_crossmodel.log")

# stable colour per model, consistent across every figure here
COLORS = model_colors(model_keys or [])


def _read_json(path: Path) -> dict:
    try:
        return json.load(open(path, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _per_model_path(key: str, fname: str) -> Path:
    return Path(products) / key / fname


def _label(key: str) -> str:
    return key.replace("/", "/\n")



CAPTIONS: dict = {}


def _save(fig, name: str, caption: str = ""):
    if caption:
        CAPTIONS[name] = caption          # always record for the captions .md file
    if caption and show_captions:
        n_key_lines = getattr(fig, "_numbered_key_lines", 0)
        ax = fig.axes[0]
        if n_key_lines:
            cap_y = -0.12 - n_key_lines * 0.055 - 0.03
            fig.text(0.5, cap_y, caption, ha="center", va="top", fontsize=7.5,
                     style="italic", wrap=True, transform=ax.transAxes)
        else:
            fig.text(0.5, -0.04, caption, ha="center", va="top", fontsize=7.5,
                     style="italic", wrap=True)
    out = FIG_DIR / name
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"wrote {out}")


# ── figures ───────────────────────────────────────────────────────────────────

def fig_match_tiers(keys: list):
    """Stacked bar: IRI / name / embedding / none — 100% of predicted triples.
    The coloured portion = overall match rate; colours show how the match was made."""
    tiers = ["iri", "name", "embedding", "none"]
    colors = {"iri": "#4C72B0", "name": "#55A868",
              "embedding": "#DD8452", "none": "#d3d3d3"}
    data = {}
    for k in keys:
        s = _read_json(_per_model_path(k, "kec_match_report.json")).get("summary", {})
        mc = s.get("method_counts")
        n = s.get("n_pred_triples")
        if mc and n:
            data[k] = {t: mc.get(t, 0) / n * 100 for t in tiers}
    if not data:
        logger.info("match_tiers: no data"); return
    mk = list(data)
    n = len(mk)
    nums = [str(i + 1) for i in range(n)]
    legend_w = 3.2
    fig_h = max(4.0, n * 0.32 + 1.2)
    fig, (ax, ax_leg) = plt.subplots(
        1, 2, figsize=(5.5 + legend_w, fig_h),
        gridspec_kw={"width_ratios": [5.5, legend_w]},
    )
    left = np.zeros(n)
    for t in tiers:
        vals = np.array([data[k][t] for k in mk])
        ax.barh(nums, vals, left=left, label=t, color=colors[t], height=0.7)
        left += vals
    # annotate total matched (= 100% - none) to the right of each bar
    for i, k in enumerate(mk):
        matched = 100 - data[k].get("none", 0)
        ax.text(101, nums[i], f"{matched:.1f}%", ha="left", va="center", fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("triples (%)")
    ax.set_ylabel("model (numbered)")
    ax.set_title("KEC extraction match rate and tier breakdown, per model")
    ax.set_xlim(0, 115)
    from matplotlib.patches import Patch as _Patch
    ax_leg.axis("off")
    tier_handles = [_Patch(facecolor=colors[t], label=t) for t in tiers]
    tier_leg = ax_leg.legend(handles=tier_handles, ncol=1, loc="lower left",
                              fontsize=9, title="match tier", title_fontsize=9,
                              frameon=True, handlelength=1.0)
    ax_leg.add_artist(tier_leg)
    numbered_legend(ax_leg, mk, COLORS, ncol=1, bbox=(0.0, 1.0), loc="upper left",
                    title="models")
    fig.tight_layout()
    _save(fig, "fig_match_rate.png",
          "Each model's LLM-extracted KEC triples broken down by how they matched "
          "the curated ground truth: IRI-exact (ontology ID), name-exact (normalised "
          "string), SapBERT embedding (cosine ≥ τ), or unmatched (grey). The "
          "percentage above each bar is the overall match rate (coloured portion). "
          "A high IRI fraction indicates the model reliably produces correct ontology IRIs.")


def fig_consensus_support(summary: dict):
    hist = summary.get("support_histogram", {})
    if not hist:
        logger.info("consensus_support: no data"); return
    xs = sorted(int(k) for k in hist)
    ys = [hist[str(x)] if str(x) in hist else hist.get(x, 0) for x in xs]
    fig, ax = plt.subplots(figsize=(max(4, len(xs) + 2), 4))
    ax.bar([str(x) for x in xs], ys, color="#55A868")
    ax.set_xlabel("number of models producing an equivalent triple (support)")
    ax.set_ylabel("triple clusters")
    ax.set_title("Cross-model agreement on extracted KEC triples")
    for i, v in enumerate(ys):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    _save(fig, "fig_consensus_support.png",
          "Cross-model agreement. Each extracted triple is clustered with equivalent "
          "triples from other models using the hybrid matcher; support = number of models "
          "independently producing it. Support ≥ 2 marks a consensus triple — a "
          "ground-truth-free trust signal available even where no curated annotation exists.")


def fig_pairwise_agreement(pairwise_tsv: Path, present: list, gt_key: str | None = None):
    # ground truth (if present) is a pseudo-model row/column — give it a distinct
    # dark swatch so it stands out from the model palette in the legend
    colors = dict(COLORS)
    if gt_key:
        colors[gt_key] = "#111111"
    gt_note = (" The 'ground_truth' row/column is the curated structured triples added "
               "as a pseudo-model: its agreement with each model is a recall-like signal "
               "(how much of the curated annotation that model reproduced), computed only "
               "over KEs that carry curated triples." if gt_key else "")
    info = pairwise_heatmap(
        pairwise_tsv, present, FIG_DIR / "fig_pairwise_agreement.png",
        title="Pairwise model agreement on KEC triples",
        colors=colors, value_label="Jaccard agreement",
        caption=(
            "Pairwise agreement (Jaccard) between models over clustered KEC triples, using "
            "the hybrid matcher's IRI + name + SapBERT-embedding tiers. Token "
            "log-probabilities and the optional LLM judge are NOT used for matching. "
            "1 = identical triple sets, 0 = disjoint. Axes numbered; models keyed below "
            "(rows/cols ordered by the bi-clustering)." + gt_note))
    if info is None:
        logger.info("pairwise: no data / <2 models")
    else:
        logger.info(f"wrote {info['out']}")


def fig_audit_verdicts(keys: list):
    order = ["correct", "partial", "wrong"]
    colors = {"correct": "#55A868", "partial": "#DD8452", "wrong": "#C44E52"}
    data = {}
    for k in keys:
        s = _read_json(_per_model_path(k, "kec_truth_audit.json")).get("summary", {})
        frac = s.get("triple_verdict_fraction") or s.get("verdict_fraction")
        if frac:
            data[k] = frac
    if not data:
        logger.info("audit_verdicts: no audited models"); return
    mk = list(data)
    n = len(mk)
    nums = [str(i + 1) for i in range(n)]
    legend_w = 3.2
    fig_h = max(4.0, n * 0.32 + 1.2)
    fig, (ax, ax_leg) = plt.subplots(
        1, 2, figsize=(5.5 + legend_w, fig_h),
        gridspec_kw={"width_ratios": [5.5, legend_w]},
    )
    left = np.zeros(n)
    for v in order:
        vals = np.array([data[k].get(v, 0) * 100 for k in mk])
        ax.barh(nums, vals, left=left, label=v, color=colors[v], height=0.7)
        left += vals
    ax.invert_yaxis()
    ax.set_xlabel("curated triples (%)")
    ax.set_ylabel("model (numbered)")
    ax.set_title("Curated KEC ground-truth reliability\nper model (self-judged)")
    ax.set_xlim(0, 100)
    from matplotlib.patches import Patch as _Patch
    ax_leg.axis("off")
    verdict_handles = [_Patch(facecolor=colors[v], label=v) for v in order]
    verdict_leg = ax_leg.legend(handles=verdict_handles, ncol=1, loc="lower left",
                                fontsize=9, title="verdict", title_fontsize=9,
                                frameon=True, handlelength=1.0)
    ax_leg.add_artist(verdict_leg)
    numbered_legend(ax_leg, mk, COLORS, ncol=1,
                    bbox=(0.0, 1.0), loc="upper left", title="models")
    fig.tight_layout()
    _save(fig, "fig_audit_verdicts.png",
          "Reliability of the curated KEC ground truth per model (self-judged): each model "
          "acts as its own judge, rating the curated structured triples correct/partial/wrong "
          "against the Key Event description. Differences across models reflect both "
          "judge-model bias and variation in the KEs each model was run on.")


def fig_logprob_auc(keys: list):
    feats = ["action_margin", "triple_logprob_mean", "response_perplexity"]
    data = {}
    for k in keys:
        s = _read_json(_per_model_path(k, "kec_logprob_calibration.json")).get("summary", {})
        pf = s.get("per_feature", {})
        if pf:
            data[k] = {fe: pf.get(fe, {}).get("auc") for fe in feats}
    if not data:
        logger.info("logprob_auc: no calibrated models"); return
    mk = list(data)
    x = np.arange(len(mk))
    w = 0.8 / len(feats)
    fig, ax = plt.subplots(figsize=(1.6 * len(mk) + 2, 4))
    for i, fe in enumerate(feats):
        vals = [data[k].get(fe) or 0 for k in mk]
        ax.bar(x + i * w, vals, w, label=fe)
    ax.axhline(0.5, ls="--", c="grey", lw=1, label="chance")
    ax.set_xticks(x + w * (len(feats) - 1) / 2)
    ax.set_xticklabels([_label(k) for k in mk], fontsize=8)
    ax.set_ylabel("ROC-AUC (predicting triple correctness)")
    ax.set_title("Log-probability trust signals, per model")
    ax.legend(fontsize=9, bbox_to_anchor=(1.01, 1), loc="upper left")
    ax.set_ylim(0, 1)
    _save(fig, "fig_logprob_auc.png",
          "Discrimination (ROC-AUC) of token-log-probability trust signals for predicting "
          "whether an extracted triple matches curated ground truth, per model. The "
          "action-margin (rank-1 minus rank-2 log-prob of the controlled-vocabulary action "
          "token) is the strongest signal; response perplexity is near chance (0.5).")


def fig_logprob_roc(keys: list):
    from sklearn.metrics import roc_curve, roc_auc_score
    from matplotlib.lines import Line2D

    # first pass: collect all curves so we can rank by AUC
    curves = []
    for k in keys:
        p = _per_model_path(k, "kec_logprob_calibration.tsv")
        try:
            rows = list(csv.DictReader(open(p, encoding="utf-8"), delimiter="\t"))
        except FileNotFoundError:
            continue
        pairs = [(int(r["correct"]), float(r["action_margin"]))
                 for r in rows if r.get("action_margin") not in ("", None)]
        ys = [c for c, _ in pairs]
        if len(set(ys)) < 2:
            continue
        y = np.array(ys); xs = np.array([m for _, m in pairs])
        fpr, tpr, _ = roc_curve(y, xs)
        curves.append((k, fpr, tpr, roc_auc_score(y, xs)))
    if not curves:
        logger.info("logprob_roc: no calibration tsv"); return

    curves.sort(key=lambda t: t[3], reverse=True)
    n_top = min(topn_models, len(curves))
    top_keys = [c[0] for c in curves[:n_top]]
    top_colors = model_colors(top_keys)

    legend_w = 3.5
    fig, (ax, ax_leg) = plt.subplots(
        1, 2, figsize=(5.0 + legend_w, 5.5),
        gridspec_kw={"width_ratios": [5.0, legend_w]},
    )
    # background: non-top models as grey
    for _, fpr, tpr, _ in curves[n_top:]:
        ax.plot(fpr, tpr, color="#cccccc", lw=0.8, alpha=0.6, zorder=1)
    # foreground: top models, coloured; build legend entries manually to include AUC
    leg_handles, leg_labels = [], []
    for i, (k, fpr, tpr, auc) in enumerate(curves[:n_top]):
        col = top_colors.get(k, "#888")
        ax.plot(fpr, tpr, color=col, lw=2.0, zorder=3)
        leg_handles.append(Line2D([0], [0], color=col, lw=2))
        leg_labels.append(f"#{i + 1} {_label(k)}  AUC={auc:.2f}")
    ax.plot([0, 1], [0, 1], ls="--", c="grey", lw=1)
    ax.set_xlabel("false positive rate")
    ax.set_ylabel("true positive rate")
    ax.set_title(f"Action-margin trust signal (ROC)\n"
                 f"(top {n_top} of {len(curves)} highlighted)")
    ax_leg.axis("off")
    ax_leg.legend(leg_handles, leg_labels, loc="upper left", fontsize=9,
                  frameon=False, handlelength=1.5, labelspacing=0.6)
    fig.tight_layout()
    _save(fig, "fig_logprob_roc.png",
          "ROC of the action-margin trust signal per model: how well the model's own "
          "confidence in the controlled-vocabulary action token ranks correct triples "
          "above incorrect ones (vs. the curated ground truth).")


def _collect_model_metrics(keys: list, blob: dict | None = None) -> dict[str, dict]:
    """Gather per-model scalar metrics for radar + scatter plots.
    blob: the crossmodel_compare summary JSON (needed for gt_coverage / consensus_agreement)."""
    import json as _json
    from collections import Counter

    # total curated GT triples — same across models, read once from any kec.json
    total_struct = 0
    for mk in keys:
        p = _per_model_path(mk, "kec.json")
        try:
            kec = _json.load(open(p, encoding="utf-8"))
            total_struct = sum(len(r.get("structured") or []) for r in kec.values())
            if total_struct:
                break
        except Exception:
            pass

    # consensus_agreement from the summary blob (mirrors smaa_rank._consensus_agreement)
    con_agree: dict = {}
    if blob:
        consensus = blob.get("consensus", {})
        per_model_blob = (blob.get("summary") or {}).get("per_model") or {}
        in_cons: Counter = Counter()
        for _ke, clusters in consensus.items():
            for cl in clusters:
                if cl.get("support", 0) >= 2:
                    for m in cl.get("members", []):
                        in_cons[m.get("model")] += 1
        for k in keys:             # iterate keys so con_agree uses the same key format
            rec = per_model_blob.get(k, {})
            pre = rec.get("consensus_agreement")
            if pre is not None:
                con_agree[k] = pre
            else:
                n = rec.get("n_llm_triples") or rec.get("n_assays") or 0
                con_agree[k] = (in_cons.get(k, 0) / n) if n else None

    raw = {}
    for k in keys:
        mc = _read_json(_per_model_path(k, "kec_match_report.json")).get("summary", {})
        lp = _read_json(_per_model_path(k, "kec_logprob_calibration.json")).get("summary", {})
        au = _read_json(_per_model_path(k, "kec_truth_audit.json")).get("summary", {})

        n_pred = mc.get("n_pred_triples") or 0
        method = mc.get("method_counts") or {}
        iri_frac = method.get("iri", 0) / n_pred if n_pred else None
        match_rate = mc.get("match_rate")

        pf = lp.get("per_feature", {})
        auc = (pf.get("action_margin") or {}).get("auc")

        tvf = au.get("triple_verdict_fraction") or {}
        audit_correct = tvf.get("correct")
        audit_wrong   = tvf.get("wrong")
        audit_score = (1 - audit_wrong) if audit_wrong is not None else None

        # gt_coverage: unique curated triples recovered ÷ total curated
        gt_cov = None
        if total_struct:
            rep = _read_json(_per_model_path(k, "kec_match_report.json"))
            covered = 0
            for _ke, matches in (rep.get("per_ke") or {}).items():
                refs = {m["ref_idx"] for m in matches
                        if m.get("matched") and m.get("ref_idx") is not None}
                covered += len(refs)
            gt_cov = covered / total_struct

        try:
            with open(_per_model_path(k, "kec.json"), encoding="utf-8") as f:
                kec = _json.load(f)
            n_ke = len(kec)
            n_triples = sum(
                len((r.get("llm_extracted") or {}).get("triples", []))
                for r in kec.values())
            tpk = n_triples / n_ke if n_ke else None
        except Exception:
            tpk = None

        raw[k] = {
            # SMAA-aligned names (used by fig_radar)
            "match_rate":          match_rate,
            "gt_coverage":         gt_cov,
            "method_iri_frac":     iri_frac,
            "action_margin_auc":   auc,
            "consensus_agreement": con_agree.get(k),
            # legacy names kept for fig_scatter_matrix
            "iri_frac":       iri_frac,
            "auc_margin":     auc,
            "audit_correct":  audit_correct,
            "audit_score":    audit_score,
            "triples_per_ke": tpk,
        }
    return raw


def fig_radar(keys: list, blob: dict | None = None):
    import pandas as pd
    metrics_raw = _collect_model_metrics(keys, blob=blob)
    # same five criteria as SMAA and parallel-coordinates
    all_dims   = ["match_rate", "gt_coverage", "method_iri_frac",
                  "action_margin_auc", "consensus_agreement"]
    all_labels = ["match\nrate", "GT\ncoverage", "IRI\nfrac",
                  "AUC\nmargin", "consensus\nagree."]

    # keep only models with at least 3 dims present
    models = [k for k in keys if sum(metrics_raw[k][d] is not None for d in all_dims) >= 3]
    if len(models) < 2:
        logger.info("radar: not enough models with data"); return

    # drop dims where every model has None (no data at all)
    dims   = [d for d in all_dims   if any(metrics_raw[k][d] is not None for k in models)]
    labels = [all_labels[all_dims.index(d)] for d in dims]

    # write raw metrics TSV (all collected fields)
    tsv_path = FIG_DIR / "model_metrics.tsv"
    all_export = ["model_key", "match_rate", "gt_coverage", "method_iri_frac",
                  "action_margin_auc", "consensus_agreement",
                  "audit_correct", "audit_score", "triples_per_ke"]
    tsv_rows = [{"model_key": k, **metrics_raw[k]} for k in keys]
    pd.DataFrame(tsv_rows)[all_export].to_csv(tsv_path, sep="\t", index=False)
    logger.info(f"wrote {tsv_path}")

    def _norm(dim):
        vals = [metrics_raw[k][dim] for k in models if metrics_raw[k][dim] is not None]
        if not vals:
            return {k: 0.0 for k in models}
        lo, hi = min(vals), max(vals)
        span = hi - lo if hi != lo else 1.0
        return {k: (metrics_raw[k][dim] - lo) / span
                if metrics_raw[k][dim] is not None else 0.0
                for k in models}

    normed = {d: _norm(d) for d in dims}

    N = len(dims)
    angles = [n / N * 2 * np.pi for n in range(N)] + [0]

    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw={"polar": True})
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["0.25", "0.5", "0.75", "1"], fontsize=6)

    # rank models by aggregate normalised score to identify leaders
    scores = {k: sum(normed[d][k] for d in dims) for k in models}
    sorted_models = sorted(models, key=lambda k: scores[k], reverse=True)
    n_top = min(topn_models, len(models))
    top_set = set(sorted_models[:n_top])
    top_list = sorted_models[:n_top]
    top_colors = model_colors(top_list)

    # background: all non-top models as faint grey
    for k in models:
        if k not in top_set:
            vals = [normed[d][k] for d in dims] + [normed[dims[0]][k]]
            ax.plot(angles, vals, lw=0.8, color="#cccccc", alpha=0.6, zorder=1)
    # foreground: top models, coloured + filled
    for k in top_list:
        col = top_colors.get(k, "#888")
        vals = [normed[d][k] for d in dims] + [normed[dims[0]][k]]
        ax.plot(angles, vals, lw=2.0, color=col, zorder=3)
        ax.fill(angles, vals, alpha=0.10, color=col)

    ax.set_title(f"Model profile (normalised, higher = better)\n(top {n_top} highlighted)", pad=18)
    numbered_legend(ax, top_list, top_colors, ncol=1, bbox=(1.12, 1.05), loc="upper left",
                    title=f"top {n_top}")
    _save(fig, "fig_radar.png",
          "Spider/radar chart of per-model quality dimensions, each normalised 0–1 "
          "relative to the model range. Only dimensions with data for ≥1 model are shown. "
          "Dimensions: match_rate (fraction of triples matching curated ground truth), "
          "iri_frac (fraction matched via exact IRI), auc_margin (action-margin ROC-AUC), "
          "audit_correct (fraction of curated triples judged correct), "
          "audit_score (1 − wrong fraction). Raw values in model_metrics.tsv.")


def fig_scatter_matrix(keys: list, blob: dict | None = None):
    import pandas as pd
    metrics_raw = _collect_model_metrics(keys, blob=blob)
    # same five SMAA criteria as radar and parallel-coordinates
    dims = ["match_rate", "gt_coverage", "method_iri_frac",
            "action_margin_auc", "consensus_agreement"]
    col_labels = {"match_rate": "match\nrate", "gt_coverage": "GT\ncoverage",
                  "method_iri_frac": "IRI\nfrac", "action_margin_auc": "AUC\nmargin",
                  "consensus_agreement": "consensus\nagree."}

    rows = []
    for i, k in enumerate(keys):
        r = {"model": str(i + 1)}
        r.update({d: metrics_raw[k][d] for d in dims})
        rows.append(r)
    df = pd.DataFrame(rows)

    avail = [d for d in dims if df[d].notna().sum() >= 2]
    if len(avail) < 2:
        logger.info("scatter_matrix: not enough data"); return

    n = len(avail)
    fig, axes = plt.subplots(n, n, figsize=(2.2 * n, 2.2 * n))
    _fallback = plt.get_cmap("tab10")

    for ri, dy in enumerate(avail):
        for ci, dx in enumerate(avail):
            ax = axes[ri][ci]
            if ri == ci:
                ax.text(0.5, 0.5, col_labels[dx], ha="center", va="center",
                        fontsize=9, transform=ax.transAxes)
                ax.set_xticks([]); ax.set_yticks([])
            else:
                for j, row in df.iterrows():
                    if row[dx] is not None and row[dy] is not None:
                        col = COLORS.get(keys[j]) or _fallback(j)
                        ax.scatter(row[dx], row[dy], color=col, s=60, zorder=3)
                        ax.text(row[dx], row[dy], row["model"],
                                fontsize=7, ha="left", va="bottom")
                ax.tick_params(labelsize=6)
            if ri == n - 1:
                ax.set_xlabel(col_labels[dx], fontsize=7)
            if ci == 0:
                ax.set_ylabel(col_labels[dy], fontsize=7)

    fig.suptitle("Pairwise model metrics scatter matrix", fontsize=10)
    # coloured, numbered key in a dedicated axes below the grid
    import math
    n_key = len(keys)
    ncol_key = 3
    key_rows = math.ceil(n_key / ncol_key)
    key_height = key_rows * 0.024 + 0.04   # figure fraction: rows × line height + padding
    plt.tight_layout(rect=[0, key_height + 0.02, 1, 0.97])
    ax_key = fig.add_axes([0.01, 0.0, 0.98, key_height])
    ax_key.axis("off")
    numbered_legend(ax_key, keys, COLORS, ncol=ncol_key, bbox=(0.0, 1.0), loc="upper left")
    # caption handled inline — don't let _save push it further
    _save(fig, "fig_scatter_matrix.png")


def _load_ke_counts(key: str) -> dict[str, dict]:
    """Return {ke_id: {gt: int, extracted: int, error: bool}} from kec.json."""
    kec = _read_json(_per_model_path(key, "kec.json"))
    out = {}
    for ke_id, rec in kec.items():
        gt = len(rec.get("structured") or [])
        llm = rec.get("llm_extracted") or {}
        extracted = len(llm.get("triples") or [])
        error = bool(rec.get("llm_error"))
        out[ke_id] = {"gt": gt, "extracted": extracted, "error": error}
    return out


def _ke_order(keys: list) -> list:
    """KE IDs sorted by ground-truth count ascending, derived from first available model."""
    for k in keys:
        counts = _load_ke_counts(k)
        if counts:
            return sorted(counts, key=lambda kid: counts[kid]["gt"])
    return []


def fig_ke_triples(key: str):
    """Per-model bar+scatter: ground-truth count (grey bar) vs extracted count (dots)."""
    counts = _load_ke_counts(key)
    if not counts:
        logger.info(f"ke_triples {key}: no data"); return

    ke_ids = sorted(counts, key=lambda kid: counts[kid]["gt"])
    n = len(ke_ids)
    gt_vals  = [counts[k]["gt"]        for k in ke_ids]
    ext_vals = [counts[k]["extracted"] for k in ke_ids]
    errors   = [counts[k]["error"]     for k in ke_ids]

    height = 5 if n < 200 else 4
    fig, ax = plt.subplots(figsize=(max(topn_models, n * 0.18), height))
    xs = np.arange(n)
    ax.bar(xs, gt_vals, color="#cccccc", label="ground truth", zorder=1)
    # normal extractions
    ok_x = [x for x, e in zip(xs, errors) if not e]
    ok_y = [y for y, e in zip(ext_vals, errors) if not e]
    ax.scatter(ok_x, ok_y, color="#4C72B0", s=12, zorder=3, label="LLM extracted")
    # errors
    err_x = [x for x, e in zip(xs, errors) if e]
    if err_x:
        ax.scatter(err_x, [0] * len(err_x), color="#C44E52", marker="x",
                   s=20, zorder=4, label="error")
    ax.set_xlim(-0.5, n - 0.5)
    ax.set_ylabel("triple count")
    ax.set_title(f"Per-KE triple counts — {key}")
    if n < 200:
        ax.set_xticks(xs)
        ax.set_xticklabels(ke_ids, rotation=90, fontsize=6, ha="center")
    else:
        ax.set_xticks([])
    ax.legend(fontsize=9, loc="upper left")
    out = _per_model_path(key, "fig_ke_triples.png")
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"wrote {out}")


def fig_ke_triples_heatmap(keys: list):
    """Heatmap: models × KEs, colour = LLM extracted count.
    KEs ordered by ground-truth count (ascending) for consistent comparison."""
    ke_ids = _ke_order(keys)
    if not ke_ids:
        logger.info("ke_triples_heatmap: no data"); return

    # ground truth counts for reference (same across models — from XML)
    gt_ref = {}
    for k in keys:
        counts = _load_ke_counts(k)
        for kid, v in counts.items():
            if kid not in gt_ref:
                gt_ref[kid] = v["gt"]

    # build matrix: rows=models, cols=KEs (NaN for missing)
    mat = np.full((len(keys), len(ke_ids)), np.nan)
    for ri, k in enumerate(keys):
        counts = _load_ke_counts(k)
        for ci, kid in enumerate(ke_ids):
            if kid in counts and not counts[kid]["error"]:
                mat[ri, ci] = counts[kid]["extracted"]

    # cluster rows (models) by extraction profile — groups similar extraction styles
    if len(keys) >= 3:
        from scipy.cluster.hierarchy import linkage, leaves_list
        from scipy.spatial.distance import pdist
        mat_filled = np.where(np.isnan(mat), 0.0, mat)
        row_order = leaves_list(linkage(pdist(mat_filled, "euclidean"), method="average"))
        mat  = mat[row_order, :]
        keys = [keys[i] for i in row_order]

    n_ke = len(ke_ids)
    n_m  = len(keys)
    fig, axes = plt.subplots(
        2, 1, figsize=(max(12, n_ke * 0.12), n_m * 0.5 + 1.5),
        gridspec_kw={"height_ratios": [1, n_m], "hspace": 0.05},
        sharex=True,
    )

    # main heatmap first so colorbar can be attached to both axes together
    ax = axes[1]
    vmax = np.nanpercentile(mat, 95) if not np.all(np.isnan(mat)) else 1
    im = ax.imshow(mat, aspect="auto", cmap="YlOrRd",
                   vmin=0, vmax=vmax, interpolation="nearest")
    ax.set_yticks(range(n_m))
    ax.set_yticklabels([str(i + 1) for i in range(n_m)], fontsize=8)
    ax.set_xlabel(f"Key Events (n={n_ke}, sorted by GT triple count; models clustered by extraction profile)",
                  fontsize=8)

    # top strip: ground-truth count — shares x with heatmap via sharex
    ax_gt = axes[0]
    ax_gt.bar(range(n_ke), [gt_ref.get(k, 0) for k in ke_ids],
              color="#aaaaaa", width=1.0)
    ax_gt.set_ylabel("GT", fontsize=7)
    ax_gt.tick_params(labelsize=6)
    ax_gt.set_title("KEC extraction per Key Event (models × KEs)", fontsize=9)

    # numbered model legend — outside the main figure to the right
    ax_leg = fig.add_axes([1.01, 0.14, 0.18, 0.84])
    ax_leg.axis("off")
    numbered_legend(ax_leg, keys, COLORS, ncol=1,
                    bbox=(0.0, 1.0), loc="upper left", title="models")
    # colorbar below the model legend, also outside — avoids stealing from the axes
    # and breaking the sharex alignment between the heatmap and the GT strip
    cbar_ax = fig.add_axes([1.02, 0.03, 0.14, 0.07])
    fig.colorbar(im, cax=cbar_ax, orientation="horizontal",
                 label="extracted triples")

    out = FIG_DIR / "fig_ke_triples_heatmap.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"wrote {out}")


if enabled:
    assert products and model_keys, "params 'products' and 'model_keys' are required"
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    blob = _read_json(Path(upstream["crossmodel_compare"]["summary"]))
    summary = blob.get("summary", {})
    present = summary.get("models_present") or list(model_keys)
    pairwise_tsv = Path(upstream["crossmodel_compare"]["pairwise"])
    gt_key = summary.get("ground_truth_key")          # GT pseudo-model, if enabled
    pw_present = present + ([gt_key] if gt_key else [])

    fig_match_tiers(list(model_keys))
    fig_consensus_support(summary)
    fig_pairwise_agreement(pairwise_tsv, pw_present, gt_key)
    fig_audit_verdicts(list(model_keys))
    fig_logprob_auc(list(model_keys))
    fig_logprob_roc(list(model_keys))
    fig_radar(list(model_keys), blob=blob)
    fig_scatter_matrix(list(model_keys), blob=blob)
    for _k in model_keys:
        fig_ke_triples(_k)
    fig_ke_triples_heatmap(list(model_keys))

    # captions file for the manuscript (one entry per generated figure)
    with open(FIG_DIR / "figure_captions.md", "w", encoding="utf-8") as f:
        f.write("# Figure captions\n\n")
        for name, cap in CAPTIONS.items():
            f.write(f"**{name}** — {cap}\n\n")
    logger.info(f"figures + captions written to {FIG_DIR}")
else:
    logger.info("figures_crossmodel disabled — skipping")
