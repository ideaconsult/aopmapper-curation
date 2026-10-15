"""
figviz.py — reusable plotting helpers.  Import-safe library (no module-level side effects).

Kept separate from the Ploomber figure tasks so the functions can be imported and called
ad hoc (the task modules execute on import and are not import-safe).
"""
from __future__ import annotations

from pathlib import Path

import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402
import numpy as np                # noqa: E402
from matplotlib.patches import Patch  # noqa: E402


# ── consistent per-model colours ──────────────────────────────────────────────

def _palette() -> list:
    """60 visually distinct qualitative colours (tab20 + tab20b + tab20c)."""
    cols = []
    for name in ("tab20", "tab20b", "tab20c"):
        cols.extend(plt.get_cmap(name).colors)
    return cols


PALETTE = _palette()


def _short(key: str) -> str:
    """Display label for a model key (drop the vendor-- prefix)."""
    return key.split("--", 1)[-1] if "--" in key else key


def model_colors(keys) -> dict:
    """Stable colour per model: unique keys sorted, mapped into the palette.
    Consistent across every figure in a run (same panel ⇒ same colours)."""
    uniq = sorted(set(keys))
    return {k: PALETTE[i % len(PALETTE)] for i, k in enumerate(uniq)}


def numbered_legend(ax, keys, colors=None, *, ncol=None, fontsize=9,
                    bbox=(0.0, -0.06), loc="upper left", title=None):
    """Coloured, numbered model key (1..N) as a proper matplotlib legend below `ax`.
    Each entry carries the model's swatch colour; numbering matches numbered axes.

    `ncol=None` (default) auto-fits the number of columns to the figure width so the
    key never spills past the plot — matplotlib otherwise sizes each column to the
    longest model name and overflows for long names / small figures. Pass an int to
    force a fixed column count."""
    labels = [f"{i + 1}. {_short(k)}" for i, k in enumerate(keys)]
    if ncol is None:
        fig_w = ax.figure.get_size_inches()[0]
        maxlen = max((len(s) for s in labels), default=1)
        # ~0.6·fontsize pt per char + swatch/padding, converted pt→inch (÷72)
        col_w = (maxlen * 0.6 * fontsize + 18) / 72.0
        ncol = max(1, min(len(keys), int(fig_w / col_w) or 1))
    handles = [Patch(facecolor=(colors.get(k) if colors else "#888"),
                     edgecolor="none", label=s)
               for s, k in zip(labels, keys)]
    return ax.legend(handles=handles, ncol=ncol, loc=loc, bbox_to_anchor=bbox,
                     fontsize=fontsize, frameon=False, handlelength=1.0,
                     handleheight=1.0, columnspacing=1.2, labelspacing=0.3,
                     title=title)


def pairwise_heatmap(pairwise_tsv, present: list, out_path, *, title: str,
                     caption: str, colors=None, value_label: str = "Jaccard agreement",
                     bicluster: bool = True):
    """Reusable numbered pairwise-agreement heatmap from a `model_a/model_b/jaccard`
    TSV. Axes are numbered 1..N (compact for large panels); models are keyed in a
    coloured legend below. Rows/cols optionally reordered by hierarchical clustering."""
    try:
        with open(pairwise_tsv, encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
    except FileNotFoundError:
        return None
    if not rows or len(present) < 2:
        return None

    idx = {k: i for i, k in enumerate(present)}
    M = np.full((len(present), len(present)), np.nan)
    np.fill_diagonal(M, 1.0)
    for r in rows:
        a, b, j = r["model_a"], r["model_b"], r.get("jaccard")
        if a in idx and b in idx and j not in ("", None):
            M[idx[a], idx[b]] = M[idx[b], idx[a]] = float(j)

    if bicluster and len(present) >= 3:
        from scipy.cluster.hierarchy import linkage, leaves_list
        from scipy.spatial.distance import squareform
        Mf = np.where(np.isnan(M), 0.0, M)
        np.fill_diagonal(Mf, 1.0)
        D = 1.0 - Mf
        np.fill_diagonal(D, 0.0)
        order = leaves_list(linkage(squareform(D), method="average"))
        present = [present[i] for i in order]
        M = M[np.ix_(order, order)]

    n = len(present)
    fig, ax = plt.subplots(figsize=(0.6 * n + 3, 0.6 * n + 3))
    im = ax.imshow(M, vmin=0, vmax=1, cmap="viridis")
    nums = [str(i + 1) for i in range(n)]
    ax.set_xticks(range(n)); ax.set_xticklabels(nums, fontsize=8)
    ax.set_yticks(range(n)); ax.set_yticklabels(nums, fontsize=8)
    for i in range(n):
        for k in range(n):
            if not np.isnan(M[i, k]):
                ax.text(k, i, f"{M[i, k]:.2f}", ha="center", va="center",
                        color="white" if M[i, k] < 0.6 else "black", fontsize=8)
    fig.colorbar(im, ax=ax, label=value_label, fraction=0.046, pad=0.04)
    ax.set_title(title)
    numbered_legend(ax, present, colors, ncol=1,    # vertical legend to the right
                    bbox=(1.12, 1.0), loc="upper left")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if caption:
        fig.text(0.5, -0.02, caption, ha="center", va="top", fontsize=7.5,
                 style="italic", wrap=True)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return {"out": str(out_path), "order": present}


def auc_explainer(labels, scores, *, model: str, feature: str,
                  out_path, caption: str | None = None):
    """
    Two-panel pedagogical figure showing HOW AUC is computed from a raw confidence
    score (no trained classifier):

      A. distributions of the score for correct vs incorrect triples, with one example
         decision threshold drawn — sliding it trades correct-kept (TPR) against
         incorrect-kept (FPR);
      B. the ROC curve traced by sweeping that threshold over the score, AUC shaded,
         with a few threshold points marked to connect back to panel A.

    labels : iterable of 0/1 (1 = triple matches curated ground truth)
    scores : iterable of float (e.g. the action-margin confidence)
    """
    from sklearn.metrics import roc_curve, roc_auc_score

    y = np.asarray(list(labels), dtype=int)
    x = np.asarray(list(scores), dtype=float)
    auc = roc_auc_score(y, x)
    fpr, tpr, thr = roc_curve(y, x)

    # an interpretable operating point: Youden's J (max TPR - FPR)
    j = np.argmax(tpr - fpr)
    t_star = thr[j]

    fig, (axA, axB) = plt.subplots(1, 2, figsize=(11.5, 4.8))

    # ── Panel A: score distributions by outcome ───────────────────────────────
    pos, neg = x[y == 1], x[y == 0]
    lo, hi = float(x.min()), float(np.percentile(x, 99))
    bins = np.linspace(lo, hi, 30)
    axA.hist(neg, bins=bins, density=True, alpha=0.6, color="#C44E52",
             label=f"incorrect (n={len(neg)})")
    axA.hist(pos, bins=bins, density=True, alpha=0.6, color="#55A868",
             label=f"correct (n={len(pos)})")
    axA.axvline(t_star, ls="--", c="black", lw=1.5)
    axA.annotate(f"example threshold t = {t_star:.2g}\n"
                 f"keep triples with score ≥ t\n"
                 f"→ TPR={tpr[j]:.2f}, FPR={fpr[j]:.2f}",
                 xy=(t_star, axA.get_ylim()[1] * 0.9),
                 xytext=(10, 0), textcoords="offset points",
                 fontsize=8, va="top",
                 bbox=dict(boxstyle="round", fc="white", ec="grey", alpha=0.9))
    axA.set_xlabel(f"{feature}  (model confidence — higher = more confident)")
    axA.set_ylabel("density")
    axA.set_title("A. Confidence score, correct vs incorrect triples")
    axA.legend(fontsize=9, loc="upper left")

    # ── Panel B: ROC traced by sweeping the threshold ─────────────────────────
    axB.plot(fpr, tpr, c="#4C72B0", lw=2)
    axB.fill_between(fpr, tpr, alpha=0.15, color="#4C72B0")
    axB.plot([0, 1], [0, 1], ls="--", c="grey", lw=1, label="chance (AUC 0.5)")
    # mark a few sweep points, including Youden's J
    marks = sorted(set([int(0.2 * (len(thr) - 1)), j,
                        int(0.8 * (len(thr) - 1))]))
    for i in marks:
        axB.plot(fpr[i], tpr[i], "o", c="black", ms=5)
        axB.annotate(f"t={thr[i]:.2g}", (fpr[i], tpr[i]), fontsize=7,
                     xytext=(6, -10), textcoords="offset points")
    axB.plot(fpr[j], tpr[j], "o", c="red", ms=7, label="Youden-optimal t")
    axB.set_xlabel("false positive rate  (incorrect triples kept)")
    axB.set_ylabel("true positive rate  (correct triples kept)")
    axB.set_title(f"B. ROC — AUC = {auc:.3f}")
    axB.legend(fontsize=9, loc="lower right")
    axB.set_xlim(0, 1); axB.set_ylim(0, 1.02)

    fig.suptitle(f"How AUC is computed from the '{feature}' trust signal — {model}",
                 fontsize=12)
    if caption is None:
        caption = (
            "No classifier is trained: the raw confidence score is itself the decision "
            "function. Sweeping a threshold over it (panel A dashed line, slid left↔right) "
            "yields a (FPR, TPR) pair at each cut; the locus of those pairs is the ROC "
            "curve (panel B). AUC is the shaded area = the probability that a randomly "
            "chosen correct triple scores higher than a randomly chosen incorrect one. "
            "AUC measures separation/ranking, not calibrated probability.")
    fig.text(0.5, -0.06, caption, ha="center", va="top", fontsize=8,
             style="italic", wrap=True)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return {"auc": float(auc), "youden_threshold": float(t_star), "out": str(out_path)}


def match_tiers(method_counts: dict, *, model: str, n_pred: int, n_matched: int,
                out_path, caption: str | None = None):
    """
    Per-model figure: how this model's extracted triples matched the curated ground
    truth, broken down by matcher tier (IRI / name / embedding), plus the unmatched
    ('none') count. `method_counts` is the matcher's per-tier tally.
    """
    order = ["iri", "name", "embedding", "none"]
    colors = {"iri": "#4C72B0", "name": "#55A868", "embedding": "#8172B3",
              "none": "#BBBBBB"}
    labels = [t for t in order if t in method_counts] + \
             [t for t in method_counts if t not in order]
    vals = [method_counts.get(t, 0) for t in labels]
    rate = (n_matched / n_pred * 100) if n_pred else 0.0

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(labels, vals, color=[colors.get(t, "#C44E52") for t in labels])
    for i, v in enumerate(vals):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("predicted triples")
    ax.set_title(f"How extracted triples matched curated truth — {model}\n"
                 f"{n_matched}/{n_pred} matched ({rate:.1f}%)")
    if caption is None:
        caption = (
            "Tier that decided each match of this model's LLM-extracted KEC triples to a "
            "curated ground-truth triple: IRI-exact, name-exact, or SapBERT embedding "
            "(≥ τ); 'none' = no curated triple matched (model novelty or curation gap).")
    fig.text(0.5, -0.04, caption, ha="center", va="top", fontsize=7.5,
             style="italic", wrap=True)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return {"out": str(out_path)}


def onto_recovery_bar(by_source: dict, *, slot: str, out_path,
                      caption: str | None = None):
    """
    Recovery rate of curated triples, grouped by the source ontology of one slot
    (process or object). `by_source` = {source: {"total": n, "recovered": n, "rate": f}},
    as written by tasks/kec_onto_recovery.py. One bar per ontology, sorted by total
    count descending, labelled with recovered/total and coloured by rate.
    """
    sources = [s for s, b in by_source.items() if b.get("total")]
    sources.sort(key=lambda s: -by_source[s]["total"])
    rates = [by_source[s]["rate"] or 0.0 for s in sources]
    totals = [by_source[s]["total"] for s in sources]
    recovered = [by_source[s]["recovered"] for s in sources]

    fig, ax = plt.subplots(figsize=(max(5, 0.8 * len(sources) + 2), 4.5))
    cmap = plt.get_cmap("RdYlGn")
    bars = ax.bar(sources, [r * 100 for r in rates],
                  color=[cmap(r) for r in rates])
    for bar, rec, tot in zip(bars, recovered, totals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f"{rec}/{tot}", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("curated triples recovered (%)")
    ax.set_ylim(0, 100)
    ax.set_title(f"Recovery of curated triples by source ontology — {slot} slot")
    if caption is None:
        caption = (
            "Recovery of curated KEC triples, grouped by the source ontology of the "
            f"{slot} slot. A triple counts as recovered when a consensus cluster "
            "(support >= min_support) matches it under the hybrid matcher.")
    fig.text(0.5, -0.04, caption, ha="center", va="top", fontsize=7.5,
             style="italic", wrap=True)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return {"out": str(out_path)}


def ontology_usage_bar(ontologies: dict, *, out_path, top: int = 15,
                       caption: str | None = None):
    """
    Which ontologies AOP-Wiki already uses. `ontologies` = {slot: {source: count}}.
    Horizontal stacked bar: one bar per ontology, stacked by slot (object/process/…),
    sorted by total usage; top-N shown.
    """
    slots = list(ontologies.keys())
    totals: dict = {}
    for slot, counts in ontologies.items():
        for src, n in counts.items():
            totals[src] = totals.get(src, 0) + n
    ranked = [s for s, _ in sorted(totals.items(), key=lambda x: -x[1])][:top]
    ranked = list(reversed(ranked))     # largest at top of horizontal bar

    fig, ax = plt.subplots(figsize=(8, max(3, 0.4 * len(ranked) + 1.5)))
    cmap = plt.get_cmap("tab10")
    left = {s: 0 for s in ranked}
    for si, slot in enumerate(slots):
        vals = [ontologies.get(slot, {}).get(s, 0) for s in ranked]
        ax.barh(ranked, vals, left=[left[s] for s in ranked],
                color=cmap(si % 10), label=slot)
        for s, v in zip(ranked, vals):
            left[s] += v
    ax.set_xlabel("curated terms")
    ax.set_title("Ontologies AOP-Wiki already uses (curated KEC / taxonomy / anatomy)")
    ax.legend(fontsize=9, ncol=min(len(slots), 4), loc="lower right")
    for s in ranked:
        ax.text(totals[s], s, f" {totals[s]}", va="center", fontsize=8)
    if caption is None:
        caption = (
            "Distinct ontology terms AOP-Wiki curators have used, by source ontology and "
            "slot. These are the preferred grounding targets — but note the annotations "
            "are reportedly ~17 years old, so IRI currency should be validated (OLS).")
    fig.text(0.5, -0.02, caption, ha="center", va="top", fontsize=7.5,
             style="italic", wrap=True)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return {"out": str(out_path)}


def grounding_bar(counts: dict, *, model: str, out_path, caption: str | None = None):
    """
    Per-model figure: ontology-grounding outcomes for object/process names —
    exact / fuzzy / none(/error) — as grouped stacked bars per slot.
    `counts` = {"object": {exact,fuzzy,none,error}, "process": {...}}.
    """
    order = ["aopwiki", "exact", "fuzzy", "none", "error"]
    colors = {"aopwiki": "#4C72B0", "exact": "#55A868", "fuzzy": "#DD8452",
              "none": "#C44E52", "error": "#888888"}
    slots = [s for s in ("object", "process") if s in counts]
    fig, ax = plt.subplots(figsize=(1.8 * len(slots) + 2, 4.2))
    for si, slot in enumerate(slots):
        bottom = 0
        total = sum(counts[slot].get(k, 0) for k in order) or 1
        for v in order:
            n = counts[slot].get(v, 0)
            if n == 0:
                continue
            ax.bar(slot, n, bottom=bottom, color=colors[v],
                   label=v if si == 0 else None)
            if n / total > 0.06:
                ax.text(si, bottom + n / 2, f"{v}\n{n}", ha="center",
                        va="center", fontsize=8, color="white")
            bottom += n
    ax.set_ylabel("object/process terms")
    ax.set_title(f"Ontology grounding of extracted names — {model}")
    _h, _l = ax.get_legend_handles_labels()      # dedupe repeated labels
    _by = dict(zip(_l, _h))
    ax.legend(_by.values(), _by.keys(), fontsize=9, loc="upper right")
    if caption is None:
        caption = (
            "Grounding of LLM-extracted object/process names. aopwiki = matches a term "
            "AOP-Wiki already uses (preferred, consistent); exact = exact hit in the "
            "external ~30-ontology dictionary; fuzzy = partial/related (review); none = "
            "no match (possible hallucination/non-standard). Action slots excluded.")
    fig.text(0.5, -0.04, caption, ha="center", va="top", fontsize=7.5,
             style="italic", wrap=True)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return {"out": str(out_path)}


def verdict_bar(verdict_fraction: dict, *, model: str, judge_model: str,
                out_path, level: str = "triple", caption: str | None = None):
    """
    Per-model figure: reliability of the curated ground truth for this model's KEs,
    as judged by `judge_model` — a single stacked bar of correct/partial/wrong.
    NB the curated triples are shared across extractors; the verdict reflects the
    judge, not the extractor.
    """
    order = ["correct", "partial", "wrong"]
    colors = {"correct": "#55A868", "partial": "#DD8452", "wrong": "#C44E52"}
    fig, ax = plt.subplots(figsize=(3.6, 4.5))
    bottom = 0.0
    for v in order:
        frac = verdict_fraction.get(v, 0) * 100
        ax.bar([model], [frac], bottom=[bottom], label=v, color=colors[v])
        if frac > 3:
            ax.text(0, bottom + frac / 2, f"{v}\n{frac:.0f}%", ha="center",
                    va="center", fontsize=8, color="white")
        bottom += frac
    ax.set_ylim(0, 100)
    ax.set_ylabel(f"curated triples (%)  [{level}-level]")
    ax.set_title(f"Curated ground-truth reliability\njudge: {judge_model}")
    ax.legend(fontsize=9, loc="upper right")
    if caption is None:
        caption = (
            "LLM-judge verdicts on the CURATED KEC triples for this model's KEs (the "
            "curation is shared across extractors; the verdict reflects the judge). "
            "correct/partial/wrong assessed against the Key Event description.")
    fig.text(0.5, -0.04, caption, ha="center", va="top", fontsize=7.5,
             style="italic", wrap=True)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return {"out": str(out_path)}
