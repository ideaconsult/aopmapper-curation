"""
figures_assays.py — Ploomber task: cross-model assay comparison figures (PNG).

GT-free analogues of the KEC crossmodel figures, reusing tasks/figviz helpers:
  - fig_assay_pairwise.png        model × model Jaccard agreement (numbered + colour key)
  - fig_assay_consensus_support.png  distribution of cross-model agreement (support counts)
  - fig_assay_landscape.png       the CONSENSUS assay catalog: structured facets, top
                                  endpoints, assays-per-KE, ontology grounding (paper figure)

Ploomber:
  upstream: ["crossmodel_assays"]
  product:  {"nb": ..., "dir": figures directory}
  params:   products, model_keys
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

from tasks.figviz import model_colors, pairwise_heatmap   # noqa: E402
from tasks.logutil import setup_logging                   # noqa: E402


# The controlled vocabularies the extraction prompt defines (tasks/extract_assays.py).
# Anything outside them is pooled into a single "other" bar rather than given its own,
# so a facet panel shows the declared vocabulary plus one honest catch-all instead of a
# long tail of one-off values. method_type itself is canonicalised upstream, at
# clustering (tasks/crossmodel_assays.py), so only genuinely off-vocabulary values reach
# here.
_VOCAB = {
    "method_type": ("in_vitro", "in_vivo", "in_silico", "ex_vivo", "not_specified"),
    "endpoint_direction": ("increase", "decrease", "change", "not_specified"),
    "throughput": ("high", "medium", "low", "not_specified"),
}


def _norm_cat(v, field: str | None = None) -> str:
    """Controlled-vocab value → lowercase token, missing → 'not_specified'.

    With `field`, a value outside that field's declared vocabulary becomes 'other'.
    """
    token = v.strip().lower() if isinstance(v, str) and v.strip() else "not_specified"
    if field is None:
        return token
    return token if token in _VOCAB.get(field, ()) else "other"


def _vocab_order(counter: Counter, field: str) -> Counter:
    """Order a facet counter by its declared vocabulary, with 'other' last."""
    ordered = Counter()
    for key in _VOCAB.get(field, ()):
        if counter.get(key):
            ordered[key] = counter[key]
    if counter.get("other"):
        ordered["other"] = counter["other"]
    return ordered


def _ontology_of(term: dict) -> str:
    """Ontology prefix for an ontology_terms entry (declared, or parsed from the IRI).
    `ontology` is the pre-AnnotatedTerm key, still present in older assays.json files."""
    ont = (term.get("source") or term.get("ontology") or "").strip().upper()
    if ont:
        return ont
    iri = term.get("iri") or ""
    frag = iri.rstrip("/").rsplit("/", 1)[-1]
    if "_" in frag:                       # obo style: GO_0006915 → GO
        return frag.split("_", 1)[0].upper()
    return "OTHER"


def _bar(ax, counter: Counter, title: str, *, top=None, horizontal=False,
         color="#4C72B0", keep_order=False):
    if keep_order:
        items = list(counter.items())[:top] if top else list(counter.items())
    else:
        items = counter.most_common(top) if top else sorted(counter.items())
    if not items:
        ax.set_visible(False)
        return
    labels = [k for k, _ in items]
    vals = [v for _, v in items]
    if horizontal:
        items = list(reversed(items))             # largest at top
        labels = [k for k, _ in items]
        vals = [v for _, v in items]
        ax.barh(range(len(vals)), vals, color=color)
        ax.set_yticks(range(len(vals)))
        ax.set_yticklabels(labels, fontsize=7)
        for i, v in enumerate(vals):
            ax.text(v, i, f" {v}", va="center", fontsize=7)
    else:
        ax.bar(range(len(vals)), vals, color=color)
        ax.set_xticks(range(len(vals)))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=7)
        for i, v in enumerate(vals):
            ax.text(i, v, str(v), ha="center", va="bottom", fontsize=7)
    ax.set_title(title, fontsize=9)
    ax.tick_params(labelsize=7)


def _short_model(m: str) -> str:
    return m.split("--", 1)[-1] if "--" in m else m


def fig_assay_reliability(products, model_keys, fig_dir, logger, ke_id=None):
    """Single-KE worked example: per-field token-logprob confidence across models.

    A heatmap (rows = each model's assay for one KE, cols = structured fields), cell
    colour = the model's mean token-logprob confidence, cell text = the extracted value.
    Reading down a column shows cross-model agreement; the instructive case is a field
    every model is confident about yet they disagree (e.g. method_type) — where logprob
    confidence alone is insufficient and cross-model consensus is required.
    """
    import numpy as np
    fields = ("assay_name", "method_type", "measured_endpoint",
              "endpoint_direction", "throughput")
    root = Path(products)
    keys = list(model_keys or [])
    data = {}
    for m in keys:
        p = root / m / "assays.json"
        if p.exists():
            try:
                data[m] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    if len(data) < 2:
        logger.info("reliability: <2 models with assays"); return

    if ke_id is None:                       # auto-pick a readable, well-covered KE
        best = None
        for ke in set().union(*[set(d) for d in data.values()]):
            lpm = [(m, d.get(ke) or []) for m, d in data.items()
                   if (d.get(ke) and any((r.get("logprobs") or {}) for r in d[ke]))]
            if len(lpm) < min(4, len(data)):
                continue
            if not all(1 <= len(a) <= 2 for _, a in lpm):
                continue
            score = (len(lpm), -sum(len(a) for _, a in lpm))
            if best is None or score > best[0]:
                best = (score, ke)
        if best is None:
            logger.info("reliability: no suitable single-KE example"); return
        ke_id = best[1]

    rows = [(m, a) for m in keys for a in (data.get(m, {}).get(ke_id) or [])]
    if not rows:
        logger.info(f"reliability: {ke_id} has no assays"); return

    nr, nc = len(rows), len(fields)
    M = np.full((nr, nc), np.nan)
    txt = [["" for _ in range(nc)] for _ in range(nr)]
    for i, (_m, a) in enumerate(rows):
        lp = a.get("logprobs") or {}
        for j, f in enumerate(fields):
            val = a.get(f)
            if isinstance(val, list):
                val = ", ".join(map(str, val))
            s = str(val) if val not in (None, "") else "—"
            lm = (lp.get(f) or {}).get("logprob_mean")
            if isinstance(lm, (int, float)):
                M[i, j] = lm
            if f == "endpoint_direction":
                mg = (lp.get(f) or {}).get("margin")
                if isinstance(mg, (int, float)):
                    s = f"{s}\n(m={mg:.1f})"
            txt[i][j] = s[:22]

    fig, ax = plt.subplots(figsize=(2.0 * nc + 2.5, max(3, 0.55 * nr + 1.6)))
    im = ax.imshow(M, cmap="RdYlGn", vmin=-1.2, vmax=0.0, aspect="auto")
    ax.set_xticks(range(nc)); ax.set_xticklabels(fields, fontsize=8,
                                                 rotation=20, ha="right")
    ax.set_yticks(range(nr))
    ax.set_yticklabels([_short_model(m) for m, _ in rows], fontsize=7)
    for i in range(nr):
        for j in range(nc):
            ax.text(j, i, txt[i][j], ha="center", va="center", fontsize=6.5)
    fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02,
                 label="confidence = mean token logprob (0 = certain)")
    n_models = len({m for m, _ in rows})
    ax.set_title(f"Per-field extraction confidence — {ke_id} "
                 f"({nr} assays, {n_models} models)", fontsize=10)
    fig.text(0.5, -0.03,
             "Each cell is one assay field; colour = the model's token-logprob confidence "
             "(green = confident, red = uncertain), text = the extracted value "
             "(endpoint_direction also shows the top-logprobs margin m). Down a column = "
             "cross-model agreement. The teaching case is a field every model is confident "
             "about yet they disagree (e.g. method_type): logprob confidence alone is not "
             "enough — cross-model consensus is what catches it.",
             ha="center", va="top", fontsize=7.5, style="italic", wrap=True)
    out = fig_dir / f"fig_assay_reliability_{ke_id}.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"wrote {out}")

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["crossmodel_assays"]
product = None
enabled = True
products = None
model_keys = None
reliability_ke = None    # KE id for the per-field confidence example; None → auto-pick
# -

FIG_DIR = Path(product["dir"])
logger = setup_logging(FIG_DIR / "figures_assays.log")
COLORS = model_colors(model_keys or [])


if enabled:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    summary = {}
    try:
        summary = json.loads(Path(upstream["crossmodel_assays"]["summary"]
                                  ).read_text(encoding="utf-8")).get("summary", {})
    except Exception as e:
        logger.warning(f"could not read summary: {e}")
    present = summary.get("models_present") or list(model_keys or [])

    # ── pairwise agreement (reuses the shared numbered/coloured heatmap) ───────
    info = pairwise_heatmap(
        upstream["crossmodel_assays"]["pairwise"], present,
        FIG_DIR / "fig_assay_pairwise.png",
        title="Pairwise model agreement on assays",
        colors=COLORS, value_label="Jaccard agreement",
        caption=(
            "Pairwise agreement (Jaccard) between models over clustered assays "
            "(assay_name + endpoint/method matched via the hybrid matcher). Assays have "
            "no ground truth; this measures inter-model reproducibility. Axes numbered; "
            "models keyed below (rows/cols ordered by the bi-clustering)."))
    logger.info("pairwise: " + (info["out"] if info else "no data / <2 models"))

    # ── consensus support distribution ────────────────────────────────────────
    hist = summary.get("support_histogram") or {}
    if hist:
        xs = sorted(hist, key=lambda x: int(x))
        ys = [hist[x] for x in xs]
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar([str(x) for x in xs], ys, color="#4C72B0")
        for i, v in enumerate(ys):
            ax.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
        ax.set_xlabel("support (number of models extracting an equivalent assay)")
        ax.set_ylabel("assay clusters")
        ax.set_title("Cross-model assay consensus support")
        fig.text(0.5, -0.03,
                 "How many models independently produced each assay. support=1 are "
                 "singletons (one model only); higher support = stronger GT-free "
                 "corroboration. min_support marks the consensus threshold.",
                 ha="center", va="top", fontsize=7.5, style="italic", wrap=True)
        out = FIG_DIR / "fig_assay_consensus_support.png"
        fig.savefig(out, dpi=140, bbox_inches="tight")
        plt.close(fig)
        logger.info(f"wrote {out}")
    else:
        logger.info("consensus support: no histogram")

    # ── assay landscape (the consensus catalog — paper figure) ─────────────────
    try:
        min_support = int(summary.get("min_support", 2))
        consensus = json.loads(Path(upstream["crossmodel_assays"]["consensus"]
                                    ).read_text(encoding="utf-8"))
        reps, per_ke = [], []
        for _ke, clusters in consensus.items():
            n = 0
            for c in clusters:
                if c.get("support", 0) >= min_support:
                    reps.append(c.get("assay") or {})
                    n += 1
            if n:
                per_ke.append(n)
        if not reps:
            logger.info("landscape: no consensus assays")
        else:
            n_assay = len(reps)
            method = _vocab_order(
                Counter(_norm_cat(a.get("method_type"), "method_type") for a in reps),
                "method_type")
            direction = _vocab_order(
                Counter(_norm_cat(a.get("endpoint_direction"), "endpoint_direction")
                        for a in reps), "endpoint_direction")
            throughput = _vocab_order(
                Counter(_norm_cat(a.get("throughput"), "throughput") for a in reps),
                "throughput")
            endpoints = Counter(
                a["measured_endpoint"].strip().lower() for a in reps
                if isinstance(a.get("measured_endpoint"), str) and a["measured_endpoint"].strip())
            onto = Counter()
            n_iri = 0
            for a in reps:
                terms = [t for t in (a.get("ontology_terms") or []) if t.get("iri")]
                if terms:
                    n_iri += 1
                for t in terms:
                    onto[_ontology_of(t)] += 1
            ppk = Counter(per_ke)

            fig, axes = plt.subplots(2, 3, figsize=(13.5, 7.2))
            _bar(axes[0, 0], method, "method type", color="#4C72B0", keep_order=True)
            _bar(axes[0, 1], direction, "endpoint direction", color="#55A868",
                 keep_order=True)
            _bar(axes[0, 2], throughput, "throughput", color="#8172B3", keep_order=True)
            _bar(axes[1, 0], endpoints, "top measured endpoints", top=12,
                 horizontal=True, color="#C44E52")
            # assays per KE
            ax = axes[1, 1]
            xs = sorted(ppk)
            ax.bar([str(x) for x in xs], [ppk[x] for x in xs], color="#DD8452")
            for i, x in enumerate(xs):
                ax.text(i, ppk[x], str(ppk[x]), ha="center", va="bottom", fontsize=7)
            ax.set_title("consensus assays per KE", fontsize=9)
            ax.set_xlabel("assays in a KE", fontsize=8)
            ax.tick_params(labelsize=7)
            # horizontal: ontology prefixes are many and the rotated x labels collided
            _bar(axes[1, 2], onto, "ontology of grounded terms", top=12,
                 horizontal=True, color="#937860")

            grounded_pct = 100.0 * n_iri / n_assay
            fig.suptitle(
                f"Consensus assay catalog (support ≥ {min_support}): "
                f"{n_assay} assays across {len(per_ke)} KEs", fontsize=12, y=1.00)
            fig.text(0.5, -0.02,
                     f"Structured assay metadata recovered from AOP-Wiki free-text "
                     f"measurement_methodology, restricted to assays corroborated by "
                     f"≥{min_support} models. {grounded_pct:.0f}% of consensus assays carry "
                     f"≥1 resolvable ontology IRI. Facets are controlled vocabularies; "
                     f"endpoints/ontologies are the most frequent observed values.",
                     ha="center", va="top", fontsize=8, style="italic", wrap=True)
            fig.tight_layout()
            out = FIG_DIR / "fig_assay_landscape.png"
            fig.savefig(out, dpi=150, bbox_inches="tight")
            plt.close(fig)
            logger.info(f"wrote {out}")

            # companion counts TSV for the paper
            import csv
            with open(FIG_DIR / "assay_landscape.tsv", "w", newline="",
                      encoding="utf-8") as f:
                w = csv.writer(f, delimiter="\t")
                w.writerow(["facet", "category", "count"])
                for facet, ctr in (("method_type", method),
                                   ("endpoint_direction", direction),
                                   ("throughput", throughput),
                                   ("measured_endpoint", endpoints),
                                   ("ontology", onto),
                                   ("assays_per_ke", ppk)):
                    for k, v in ctr.most_common():
                        w.writerow([facet, k, v])
                w.writerow(["summary", "n_consensus_assays", n_assay])
                w.writerow(["summary", "n_kes", len(per_ke)])
                w.writerow(["summary", "pct_with_iri", round(grounded_pct, 1)])
            logger.info(f"wrote {FIG_DIR/'assay_landscape.tsv'}")
    except Exception as e:
        logger.warning(f"landscape figure skipped: {e}")

    # ── single-KE reliability example (per-field logprob confidence) ───────────
    try:
        fig_assay_reliability(products, present, FIG_DIR, logger,
                              ke_id=reliability_ke)
    except Exception as e:
        logger.warning(f"reliability figure skipped: {e}")
else:
    logger.info("figures_assays disabled — skipping")
