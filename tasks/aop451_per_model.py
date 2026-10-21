"""
aop451_per_model.py — per-model AOP 451 reference-check block and the main-text per-model table.
Import-safe library used by tasks/export_manuscript_results.py (the export_aop451 task).

Reads aop451_reference_check.py's json (results[model]['kec'] / ['assays']) and produces
(1) a per-model list plus min/max/median summary for aop451_stats.json and (2) a generated
pandoc-crossref markdown fragment (pipe table + caption, {#tbl:aop451-per-model}) that the
manuscript includes verbatim.

Also the pooled-panel view (panel_kec_recovery + the {#tbl:aop451-kec} fragment): for each
curated KEC triple, how many models recover it; and the pooled assay figure (assay_union +
the {#tbl:aop451-assays-ref} fragment): for each recommended method, how many models match
it in their own result, the union being the pooled-panel number.
"""
from __future__ import annotations

import re
import statistics
from pathlib import Path

TABLE_HEADER = ["Model", "KEC triples recovered", "KEC recall (%)",
                "Recommended methods matched", "Matched (%)"]

# Trailing quantisation / precision / packaging tokens dropped from a Hugging Face model name
# (applied repeatedly, in any order, from the end of the name), then instruct-release tags.
_QUANT_SUFFIXES = ("-vllm", "-NVFP4", "-AWQ", "-FP8", "-A12B")
_RELEASE_SUFFIXES = ("-Instruct-2512",)
_PRISMA = re.compile(r"-PrismaQuant-(\d+(?:\.\d+)?)bit$")
_COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
                8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}


def short_name(model_key: str) -> str:
    """Same rule as aop451_reference_check._short (kept in sync by hand: importing that
    module would run its Ploomber task body)."""
    name = model_key.split("--")[-1]
    return name.replace("-A12B-NVFP4", "").replace("-A4B-it", "")


def hf_id(model_key: str) -> str:
    """Model keys are Hugging Face ids with "/" written as "--" (org--name)."""
    return model_key.replace("--", "/", 1)


def display_name(model_key: str) -> str:
    """Readable table name from the HF id: drop the org prefix, a leading "NVIDIA-", the
    quantisation/precision suffixes and the Instruct-2512 tag; a PrismaQuant bit-width
    becomes " (<w> bit)" so differently quantised copies of one model stay distinct."""
    name = model_key.split("--", 1)[-1]
    if name.startswith("NVIDIA-"):
        name = name[len("NVIDIA-"):]
    bits = ""
    changed = True
    while changed:
        changed = False
        for suf in _QUANT_SUFFIXES:
            if name.endswith(suf):
                name, changed = name[:-len(suf)], True
        m = _PRISMA.search(name)
        if m:
            bits = f" ({m.group(1)} bit)"
            name, changed = name[:m.start()], True
    for suf in _RELEASE_SUFFIXES:
        if name.endswith(suf):
            name = name[:-len(suf)]
    return name + bits


def per_model_rows(results: dict) -> list[dict]:
    """One row per model in the reference-check json, in the json's order."""
    rows = []
    for model, res in (results or {}).items():
        kec = res.get("kec") or {}
        assays = res.get("assays") or {}
        rows.append({
            "model": model,
            "short_name": short_name(model),
            "kec_n_matched": kec.get("n_matched_total"),
            "kec_n_curated": kec.get("n_curated_total"),
            "kec_recall": kec.get("recall"),
            "assay_n_recovered": assays.get("n_recovered"),
            "assay_n_reference": assays.get("n_reference_assays"),
            "assay_recovery_rate": assays.get("recovery_rate"),
        })
    return rows


def _extreme(rows: list[dict], field: str, pick) -> dict | None:
    scored = [r for r in rows if r.get(field) is not None]
    if not scored:
        return None
    value = pick(r[field] for r in scored)
    at = [r for r in scored if r[field] == value]
    return {"value": value,
            "models": [r["model"] for r in at],
            "short_names": [r["short_name"] for r in at]}


def summarise(rows: list[dict], field: str) -> dict:
    """min / max / median of `field` over the models that have it; min and max carry every
    model tied at the extreme (recall is a ratio of small integers, so ties are common)."""
    vals = [r[field] for r in rows if r.get(field) is not None]
    return {
        "n_models": len(vals),
        "min": _extreme(rows, field, min),
        "max": _extreme(rows, field, max),
        "median": statistics.median(vals) if vals else None,
    }


def per_model_block(results: dict) -> dict:
    """The `per_model_summary` block of aop451_stats.json."""
    rows = per_model_rows(results)
    return {
        "n_models": len(rows),
        "models": rows,
        "summary": {
            "kec_recall": summarise(rows, "kec_recall"),
            "assay_recovery_rate": summarise(rows, "assay_recovery_rate"),
        },
    }


def _pct(v) -> str:
    return "" if v is None else f"{100 * v:.1f}"


def _cell(v) -> str:
    return "" if v is None else str(v)


def _count_word(n: int) -> str:
    return _COUNT_WORDS.get(n, str(n))


def render_table(rows: list[dict], panel: dict | None = None) -> str:
    """The markdown fragment: pipe table, blank line, pandoc-crossref caption line.

    `panel` (from `panel_pooled_row`) appends the bold `**Panel (pooled)**` row and the
    sentence in the caption that states how the panel is pooled."""
    lines = [
        "| " + " | ".join(TABLE_HEADER) + " |",
        "|" + "|".join(["---"] + ["---:"] * (len(TABLE_HEADER) - 1)) + "|",
    ]
    for r in rows:
        cells = [display_name(r["model"]), _cell(r["kec_n_matched"]), _pct(r["kec_recall"]),
                 _cell(r["assay_n_recovered"]), _pct(r["assay_recovery_rate"])]
        lines.append("| " + " | ".join(cells) + " |")
    if panel is not None:
        cells = ["Panel (pooled)", _cell(panel["kec_n_matched"]), _pct(panel["kec_recall"]),
                 _cell(panel["assay_n_recovered"]), _pct(panel["assay_recovery_rate"])]
        lines.append("| " + " | ".join(f"**{c}**" for c in cells) + " |")
    n_cur = next((r["kec_n_curated"] for r in rows if r["kec_n_curated"] is not None), "")
    n_ref = next((r["assay_n_reference"] for r in rows if r["assay_n_reference"] is not None), "")
    caption = (f"Table: AOP 451 reference check for each model of the {_count_word(len(rows))}-model "
               f"panel: recovery of the {n_cur} curated KEC triples of the pathway in AOP-Wiki, and "
               f"match to the {n_ref} standardized assays recommended in Figure 1 of @nymark2021.")
    if panel is not None:
        caption += " " + _pooling_sentence(panel)
    caption += " {#tbl:aop451-per-model}"
    return "\n".join(lines) + "\n\n" + caption + "\n"


def _pooling_sentence(panel: dict) -> str:
    k = _count_word(panel["pool_min_support"])
    n = _count_word(panel["n_models"])
    return (f"The last row pools the panel: a curated triple counts as recovered if at least "
            f"{k} of the {n} models finds it, and a recommended method as matched if at "
            f"least {k} of the {n} models matches it on its own.")


def write_table(rows: list[dict], md_path: Path, panel: dict | None = None) -> int:
    """Write the generated markdown fragment. Returns the number of models written."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_table(rows, panel), encoding="utf-8", newline="\n")
    return len(rows)


# ── Panel-level KEC recovery ──────────────────────────────────────────────────
# The per-model block above answers "how much does each model recover"; this one pools
# the panel: for each curated triple, how many models recover it. Reads the same
# results[model]['kec']['per_ke'][KE]['match_verdicts'] (one verdict per LLM triple;
# ref_idx indexes the KE's curated triples). Unmatched verdicts also carry a ref_idx (the
# nearest curated triple) and must be ignored: only matched==true counts.

PANEL_TABLE_HEADER = ["Key Event", "Curated triples", "Recovered by at least 1 model",
                      "Recovered by at least {k} models", "Best single model"]

# The existing hand-written reference table (results_AOP451.md) labels these two KEs by
# their role in the pathway rather than by the AOP-Wiki title.
_KE_LABEL_OVERRIDES = {
    "KE1495": "Membrane interaction with lung resident cells (MIE)",
    "KE1670": "Lung cancer (AO)",
}


def short_ke_title(ke_id: str, title: str | None) -> str:
    """Short Key Event label as in the manuscript's reference table: the AOP-Wiki title
    sentence-cased ("Increase, Cell Proliferation" -> "Increase, cell proliferation"),
    acronyms kept, the comma after a leading "Increased" dropped, and the MIE / AO labelled
    by role."""
    if ke_id in _KE_LABEL_OVERRIDES:
        return _KE_LABEL_OVERRIDES[ke_id]
    words = (title or ke_id).split()
    out = [w if (i == 0 or any(c.isupper() for c in w[1:])) else w.lower()
           for i, w in enumerate(words)]
    text = " ".join(out)
    if text.startswith("Increased, "):
        text = "Increased " + text[len("Increased, "):]
    return text


def _kec_per_ke(res: dict) -> dict:
    return ((res or {}).get("kec") or {}).get("per_ke") or {}


def check_match_semantics(results: dict) -> dict:
    """Verify that, for every model and KE, the number of DISTINCT ref_idx among the
    verdicts with matched==true equals per_ke[KE]['n_matched'] (greedy one-to-one), that
    every matched ref_idx is a valid index into the KE's curated triples, and that a KE's
    n_curated is the same for every model. Returns counts and the list of violations; the
    caller decides whether to stop."""
    violations = []
    n_checked = 0
    n_duplicate_matched_ref_idx = 0
    n_curated_seen: dict[str, int] = {}
    for model, res in (results or {}).items():
        for ke, rec in _kec_per_ke(res).items():
            n_checked += 1
            n_cur = rec.get("n_curated") or 0
            if n_curated_seen.setdefault(ke, n_cur) != n_cur:
                violations.append({"model": model, "ke_id": ke, "kind": "n_curated_differs",
                                   "n_curated": n_cur, "expected": n_curated_seen[ke]})
            matched = [v.get("ref_idx") for v in rec.get("match_verdicts") or []
                       if v.get("matched")]
            distinct = set(matched)
            n_duplicate_matched_ref_idx += len(matched) - len(distinct)
            if len(distinct) != rec.get("n_matched"):
                violations.append({"model": model, "ke_id": ke, "kind": "n_matched_mismatch",
                                   "distinct_matched_ref_idx": len(distinct),
                                   "n_matched": rec.get("n_matched")})
            if any(not isinstance(i, int) or not 0 <= i < n_cur for i in distinct):
                violations.append({"model": model, "ke_id": ke, "kind": "ref_idx_out_of_range",
                                   "ref_idx": sorted(map(str, distinct)), "n_curated": n_cur})
    return {"n_model_ke_checked": n_checked,
            "n_violations": len(violations),
            "n_duplicate_matched_ref_idx": n_duplicate_matched_ref_idx,
            "violations": violations}


def panel_kec_recovery(results: dict, min_support: int) -> dict:
    """The `panel_kec_recovery` block of aop451_stats.json.

    Per KE (reference order = order of the first model's per_ke) and per curated triple,
    the number of models recovering it. `min_support` is the consensus threshold from the
    config (not hardcoded here); `n_recovered_ge_min_support` counts curated triples
    recovered by at least that many models. best_single_* is the best individual model
    (all tied names). In `totals`, best_single is the best model on the whole pathway, which
    is NOT the sum of the per-KE bests (different models win different KEs)."""
    check = check_match_semantics(results)
    if check["n_violations"]:
        raise ValueError(
            f"match_verdicts semantics violated for {check['n_violations']} (model, KE) "
            f"pairs, e.g. {check['violations'][:3]}")
    models = [m for m, res in results.items() if _kec_per_ke(res)]
    ke_order: list[str] = []
    for m in models:
        for ke in _kec_per_ke(results[m]):
            if ke not in ke_order:
                ke_order.append(ke)

    per_ke = []
    model_totals = {m: 0 for m in models}
    for ke in ke_order:
        recs = {m: _kec_per_ke(results[m])[ke] for m in models if ke in _kec_per_ke(results[m])}
        first = next(iter(recs.values()))
        n_cur = first.get("n_curated") or 0
        support = [0] * n_cur
        n_matched = {}
        for m, rec in recs.items():
            hit = {v["ref_idx"] for v in rec.get("match_verdicts") or [] if v.get("matched")}
            for i in hit:
                support[i] += 1
            n_matched[m] = len(hit)
            model_totals[m] += len(hit)
        best = max(n_matched.values(), default=0)
        best_models = [m for m, n in n_matched.items() if n == best]
        per_ke.append({
            "ke_id": ke,
            "title": first.get("title"),
            "short_title": short_ke_title(ke, first.get("title")),
            "n_curated": n_cur,
            "n_recovered_any": sum(1 for s in support if s >= 1),
            "n_recovered_ge_min_support": sum(1 for s in support if s >= min_support),
            "best_single_n": best,
            "best_single_models": best_models,
            "best_single_short_names": [short_name(m) for m in best_models],
            "n_models_any": sum(1 for n in n_matched.values() if n >= 1),
            "triple_support": support,
        })

    all_support = [s for r in per_ke for s in r["triple_support"]]
    best_total = max(model_totals.values(), default=0)
    best_total_models = [m for m, n in model_totals.items() if n == best_total]
    n_cur_total = len(all_support)
    n_any = sum(1 for s in all_support if s >= 1)
    return {
        "n_models": len(models),
        "models": models,
        "min_support": min_support,
        "semantic_check": {k: check[k] for k in
                           ("n_model_ke_checked", "n_violations", "n_duplicate_matched_ref_idx")},
        "per_ke": per_ke,
        "totals": {
            "n_curated": n_cur_total,
            "n_recovered_any": n_any,
            "n_recovered_ge_min_support": sum(1 for s in all_support if s >= min_support),
            "best_single_n": best_total,
            "best_single_models": best_total_models,
            "best_single_short_names": [short_name(m) for m in best_total_models],
            "sum_of_per_ke_best_single": sum(r["best_single_n"] for r in per_ke),
        },
        "n_curated_triples_unrecovered": n_cur_total - n_any,
        "triple_support": all_support,
        "support_distribution": {str(k): sum(1 for s in all_support if s == k)
                                 for k in range(len(models) + 1)},
    }


# ── Panel-level assay union ───────────────────────────────────────────────────
# A recommended method counts as matched by the panel if ANY model matches it in its own
# per-model result (results[model]['assays']['assays'][i]['recovered']). This is a true
# union of the per-model columns: it can never be smaller than the best member. (The
# earlier pooled figure, aop451_reference_check's `consensus_assays`, re-matched the
# reference against one *representative* assay per cross-model cluster; a model whose
# matching assay is not its cluster's representative was lost, so that figure could fall
# below the best single model. That block is still written by the reference check, but it
# is not used for the table.)

def assay_reference_rows(results: dict) -> tuple[list[str], list[dict]]:
    """The reference methods in reference order, shared by every model.

    Returns (models, rows) with one row per Figure 1B method
    ({reference_assay, guideline, relevant_ke_groups}). Asserts that every model's
    assay rows list the same methods in the same order (index i is the same method for
    all), since the union is taken by index. Raises ValueError if a model has no assay
    rows: the union would silently shrink."""
    models = list(results or {})
    if not models:
        raise ValueError("no models in reference-check results")
    ref_rows = None
    for m in models:
        rows = ((results[m] or {}).get("assays") or {}).get("assays")
        if not rows:
            raise ValueError(f"model {m} has no per-method assay results")
        key = [(r["reference_assay"], r.get("guideline"), list(r.get("relevant_ke_groups") or []))
               for r in rows]
        if ref_rows is None:
            ref_rows, ref_model = key, m
        else:
            assert key == ref_rows, (
                f"reference method order differs between {ref_model} and {m}")
    return models, [{"reference_assay": n, "guideline": g, "relevant_ke_groups": k}
                    for n, g, k in ref_rows]


def assay_union(results: dict) -> dict:
    """Union of the models' own matches against the recommended methods.

    Per method: which models match it (`models_matching`, in results order) and how many.
    `n_matched_any` is the number of methods with at least one matching model. The result
    is checked against each model's reported n_recovered, and `n_matched_any` is checked to
    be >= the best single model (it cannot be smaller; if it is, the inputs are inconsistent).
    """
    models, ref_rows = assay_reference_rows(results)
    methods = []
    per_model = {m: 0 for m in models}
    for i, ref in enumerate(ref_rows):
        hit = [m for m in models if results[m]["assays"]["assays"][i]["recovered"]]
        for m in hit:
            per_model[m] += 1
        methods.append({**ref, "n_models_matching": len(hit), "models_matching": hit})
    for m in models:
        reported = results[m]["assays"].get("n_recovered")
        if reported is not None:
            assert per_model[m] == reported, (
                f"{m}: {per_model[m]} recovered rows != reported n_recovered {reported}")
    n_any = sum(1 for r in methods if r["n_models_matching"] >= 1)
    best = max(per_model.values())
    assert n_any >= best, f"union {n_any} smaller than best single model {best}"
    n_ref = len(methods)
    return {
        "n_models": len(models),
        "models": models,
        "n_reference_assays": n_ref,
        "n_matched_any": n_any,
        "matched_any_rate": n_any / n_ref if n_ref else None,
        "per_model_n_recovered": per_model,
        "best_single_n": best,
        "best_single_models": [m for m, n in per_model.items() if n == best],
        "methods": methods,
    }


ASSAYS_REF_HEADER = ["Recommended method (Figure 1B)", "KE group", "Models matching (of {n})"]
ASSAYS_REF_TOTAL_LABEL = "Methods matched by at least one model"

# Grouping sentence of the caption (copied from the previous hand-written caption).
_KE_GROUP_SENTENCE = (
    "The KE group column follows the Key-Event grouping of Figure 1 of @nymark2021 "
    "(KE1A inflammatory response, KE1B/KE2 genotoxicity, KE1C oxidative-stress responses, "
    "KE3 mutagenicity, KE4 proliferation).")


def render_assays_ref_table(union: dict) -> str:
    """Markdown fragment of @tbl:aop451-assays-ref: one row per recommended method (method
    name and KE group from the reference-check json, i.e. the reference fixture), the
    number of models whose own result matches it (0 shown as '-'), and a bold last row
    with the union count."""
    n = union["n_models"]
    header = [h.format(n=n) for h in ASSAYS_REF_HEADER]
    lines = ["| " + " | ".join(header) + " |", "|---|---|---:|"]
    for r in union["methods"]:
        k = r["n_models_matching"]
        lines.append("| " + " | ".join(
            [r["reference_assay"], ", ".join(r["relevant_ke_groups"]), str(k) if k else "-"])
            + " |")
    lines.append(f"| **{ASSAYS_REF_TOTAL_LABEL}** | | "
                 f"**{union['n_matched_any']} / {union['n_reference_assays']}** |")
    caption = (f"Table: Standardized methods recommended in Figure 1B of @nymark2021, and how "
               f"many of the {_count_word(n)} models propose a matching assay for each. "
               f"{_KE_GROUP_SENTENCE} {{#tbl:aop451-assays-ref}}")
    return "\n".join(lines) + "\n\n" + caption + "\n"


def write_assays_ref_table(union: dict, md_path: Path) -> int:
    """Write the fragment. Returns the number of method rows written."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_assays_ref_table(union), encoding="utf-8", newline="\n")
    return len(union["methods"])


# Pooling rule of the "Panel (pooled)" row of the per-model table: a curated KEC triple is
# recovered if >= 1 model recovers it (panel_kec_recovery `n_recovered_any`), and a
# recommended method is matched if >= 1 model matches it (assay_union `n_matched_any`).
POOL_MIN_SUPPORT = 1


def panel_pooled_row(panel_kec: dict, union: dict | None) -> dict:
    """The pooled-panel row of the per-model table, read from the export's own outputs:
    `panel_kec_recovery` (totals.n_recovered_any / n_curated) and `assay_union`
    (n_matched_any / n_reference_assays). Raises if the assay union is missing: the row
    must not be written with a guessed or blank assay cell."""
    if not union or union.get("n_matched_any") is None:
        raise ValueError("pooled-panel assay score (assay_union over the per-model "
                         "reference-check results) is missing")
    t = panel_kec["totals"]
    n_cur = t["n_curated"]
    return {
        "n_models": panel_kec["n_models"],
        "pool_min_support": POOL_MIN_SUPPORT,
        "kec_n_matched": t["n_recovered_any"],
        "kec_n_curated": n_cur,
        "kec_recall": (t["n_recovered_any"] / n_cur) if n_cur else None,
        "assay_n_recovered": union["n_matched_any"],
        "assay_n_reference": union.get("n_reference_assays"),
        "assay_recovery_rate": union.get("matched_any_rate"),
        "assay_n_panel_models": union.get("n_models"),
    }


def render_panel_table(block: dict) -> str:
    """Markdown fragment for the pooled-panel table: pipe table, bold Total row, blank
    line, pandoc-crossref caption ({#tbl:aop451-kec})."""
    k = block["min_support"]
    header = [h.format(k=k) for h in PANEL_TABLE_HEADER]
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] + ["---:"] * (len(header) - 1)) + "|"]
    for r in block["per_ke"]:
        lines.append("| " + " | ".join(
            [r["short_title"], str(r["n_curated"]), str(r["n_recovered_any"]),
             str(r["n_recovered_ge_min_support"]), str(r["best_single_n"])]) + " |")
    t = block["totals"]
    lines.append("| " + " | ".join(
        [f"**{c}**" for c in ("Total", t["n_curated"], t["n_recovered_any"],
                              t["n_recovered_ge_min_support"], t["best_single_n"])]) + " |")
    caption = (f"Table: Recovery of the {t['n_curated']} curated KEC triples of AOP 451 by the "
               f"{_count_word(block['n_models'])}-model panel pooled: curated triples found by at "
               f"least one model, and by at least {_count_word(k)} models, against the best "
               "individual model. Key Events with no curated triple are listed with zeros. "
               "{#tbl:aop451-kec}")
    return "\n".join(lines) + "\n\n" + caption + "\n"


def write_panel_table(block: dict, md_path: Path) -> int:
    """Write the pooled-panel fragment. Returns the number of Key Event rows written."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_panel_table(block), encoding="utf-8", newline="\n")
    return len(block["per_ke"])
