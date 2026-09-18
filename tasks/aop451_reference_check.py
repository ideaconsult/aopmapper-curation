"""
aop451_reference_check.py — Ploomber task: score a model's KEC and assay extractions
against the AOP 451 reference (resources/aop451_reference.json), transcribed from Nymark
et al. (2021, Frontiers in Toxicology, doi:10.3389/ftox.2021.653386), Figure 1.

AOP 451 has curated KEC triples in AOP-Wiki, but its assays do not — this is the one
worked case in the assay track with an actual published reference to check against
(the paper's Figure 1B panel of 18 recommended standardized methods), rather than relying
solely on the ground-truth-free proxies (consensus, evidence grounding) used for the
other ~630 KEs in the assay track. Acts as a fixed-fixture regression check, similar in
spirit to a unit test but requiring live model output, so it lives as a Ploomber task
rather than in tests/ (which is deliberately network/model-free).

Ploomber:
  upstream: none (reads finished per-model products directly from disk)
  product:  {"json": full comparison, "csv": per-model summary row}
  params:   kec_products_dir, assays_products_dir, model_keys, reference_path,
            embedding_model, tau
"""
from __future__ import annotations

import json
from pathlib import Path

from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
product = None
enabled = True
kec_products_dir = None     # root holding per-model kec.json / kec_match_report.json
assays_products_dir = None  # root holding per-model assays.json
model_keys = []              # models to score against the reference
reference_path = "resources/aop451_reference.json"
embedding_model = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
tau = 0.70                   # assay-name matching threshold, matches assay_compare default
viz_assay_consensus_json = None  # optional: assay consensus clusters, to score the PANEL
                                  # against the same reference as the individual models
# -

logger = setup_logging(Path(product["json"]).parent / "aop451_reference_check.log")


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        return json.load(f)


def _all_ke_ids(reference: dict) -> list[str]:
    ids = []
    for group in reference["key_events"]:
        for ke in group["ke_ids"]:
            if ke not in ids:
                ids.append(ke)
    return ids


def _score_kec(model_key: str, ke_ids: list[str]) -> dict:
    """Per-model KEC recall against the curated AOP-Wiki triples for each reference KE
    (the same curated/LLM/match_report artefacts used by the KEC116 crossmodel pipeline;
    §kec_compare.py), not against the paper directly — the paper does not itself publish
    KEC triples, only the KE structure and standardized-assay panel."""
    kec_path = Path(kec_products_dir) / model_key / "kec.json"
    match_path = Path(kec_products_dir) / model_key / "kec_match_report.json"
    if not kec_path.exists():
        logger.warning(f"KEC: missing {kec_path}")
        return {}
    kec = _load_json(kec_path)
    match = _load_json(match_path) if match_path.exists() else {"per_ke": {}}
    per_ke = match.get("per_ke", {})

    rows = {}
    n_curated_total = n_matched_total = 0
    for ke_id in ke_ids:
        entry = kec.get(ke_id)
        if entry is None:
            continue
        curated = entry.get("structured") or []
        verdicts = per_ke.get(ke_id, [])
        n_matched = sum(1 for v in verdicts if v.get("matched"))
        n_curated_total += len(curated)
        n_matched_total += n_matched
        rows[ke_id] = {
            "title": entry.get("title"),
            "n_curated": len(curated),
            "n_llm_triples": len((entry.get("llm_extracted") or {}).get("triples", [])),
            "n_matched": n_matched,
            "match_verdicts": verdicts,
        }
    return {
        "per_ke": rows,
        "n_curated_total": n_curated_total,
        "n_matched_total": n_matched_total,
        "recall": (n_matched_total / n_curated_total) if n_curated_total else None,
    }


def _score_assays(model_key: str, ke_ids: list[str], reference: dict,
                   embedder) -> dict:
    """Match each reference standardized assay (Figure 1B) against every assay the model
    extracted for KEs in that assay's relevant_ke_groups, using the same assay_match
    primitive the cross-model consensus pipeline uses. A reference assay is 'recovered'
    if the model extracted an equivalent assay (by name + endpoint/method_type) for any
    KE in its relevant group — this is deliberately lenient about *which* KE within a
    group carries the match, since Figure 1B assigns assays to KE groups, not single KEs."""
    from tasks.assay_compare import assay_match

    assays_path = Path(assays_products_dir) / model_key / "assays.json" if assays_products_dir else None
    if not assays_path or not assays_path.exists():
        logger.warning(f"assays: missing {assays_path}")
        return {}
    assays = _load_json(assays_path)

    group_by_ke = {}
    for group in reference["key_events"]:
        for ke_id in group["ke_ids"]:
            group_by_ke.setdefault(ke_id, set()).add(group["group"])

    rows = []
    for ref_assay in reference["standardized_assays"]:
        best = None
        for ke_id in ke_ids:
            ke_groups = group_by_ke.get(ke_id, set())
            if not (ke_groups & set(ref_assay["relevant_ke_groups"])):
                continue
            for rec in assays.get(ke_id, []) or []:
                matched, score, method = assay_match(
                    ref_assay, rec, embedder=embedder, tau=tau)
                if matched and (best is None or score > best["score"]):
                    best = {"ke_id": ke_id, "assay_name": rec.get("assay_name"),
                            "score": score, "method": method}
        rows.append({
            "reference_assay": ref_assay["assay_name"],
            "guideline": ref_assay["guideline"],
            "relevant_ke_groups": ref_assay["relevant_ke_groups"],
            "recovered": best is not None,
            "best_match": best,
        })

    n_recovered = sum(1 for r in rows if r["recovered"])
    return {
        "assays": rows,
        "n_reference_assays": len(rows),
        "n_recovered": n_recovered,
        "recovery_rate": (n_recovered / len(rows)) if rows else None,
    }


def _score_assays_consensus(ke_ids: list[str], reference: dict, embedder) -> dict:
    """Same scoring as _score_assays, but the candidate set is the panel's *consensus
    clusters* rather than one model's assays.

    This is what makes the single-model and panel columns of the case-study table
    comparable: identical reference fixture, identical assay_match primitive, identical
    tau, differing only in whose assays are being matched. Reading the panel figure off
    the per-KE consensus table by eye - as the manuscript previously did - cannot
    guarantee that, and is the reason this exists.

    Each cluster contributes its representative assay plus the support count, so a
    recovered reference assay carries how many models backed the cluster that matched
    it - the evidence a curator would weigh, not just a yes/no."""
    if not viz_assay_consensus_json or not Path(viz_assay_consensus_json).exists():
        logger.warning(f"consensus: missing {viz_assay_consensus_json}")
        return {}
    from tasks.assay_compare import assay_match

    viz = _load_json(Path(viz_assay_consensus_json))
    n_panel = len((viz.get("summary") or {}).get("models") or [])
    by_ke: dict[str, list] = {}
    for ke in viz.get("kes", []):
        by_ke[ke.get("ke_id")] = ke.get("clusters") or []

    group_by_ke = {}
    for group in reference["key_events"]:
        for ke_id in group["ke_ids"]:
            group_by_ke.setdefault(ke_id, set()).add(group["group"])

    rows = []
    for ref_assay in reference["standardized_assays"]:
        best = None
        for ke_id in ke_ids:
            ke_groups = group_by_ke.get(ke_id, set())
            if not (ke_groups & set(ref_assay["relevant_ke_groups"])):
                continue
            for cluster in by_ke.get(ke_id, []):
                rec = cluster.get("assay") or {}
                matched, score, method = assay_match(
                    ref_assay, rec, embedder=embedder, tau=tau)
                if matched and (best is None or score > best["score"]):
                    best = {"ke_id": ke_id, "assay_name": rec.get("assay_name"),
                            "score": score, "method": method,
                            "support": cluster.get("support")}
        rows.append({
            "reference_assay": ref_assay["assay_name"],
            "guideline": ref_assay["guideline"],
            "relevant_ke_groups": ref_assay["relevant_ke_groups"],
            "recovered": best is not None,
            "best_match": best,
        })

    n_recovered = sum(1 for r in rows if r["recovered"])
    return {
        "assays": rows,
        "n_panel_models": n_panel,
        "n_reference_assays": len(rows),
        "n_recovered": n_recovered,
        "recovery_rate": (n_recovered / len(rows)) if rows else None,
    }


def _short(model_key: str) -> str:
    name = model_key.split("--")[-1]
    return name.replace("-A12B-NVFP4", "").replace("-A4B-it", "")


def _write_markdown_tables(path: Path, reference: dict, ke_ids: list[str],
                            model_keys: list[str], results: dict) -> None:
    """Two ready-to-paste Markdown tables: per-KE curated-vs-matched counts (one column
    pair per model), and per-assay recovered/not-recovered. Regenerate and re-paste into
    results.md any time the underlying products or reference fixture change."""
    ke_group_by_id: dict[str, str] = {}
    for group in reference["key_events"]:
        for ke_id in group["ke_ids"]:
            ke_group_by_id[ke_id] = group["group"]

    lines = ["## AOP 451 reference check", ""]
    lines.append(f"Source: {reference['reference']['citation']} "
                 f"(doi:{reference['reference']['doi']}), {reference['reference']['figure']}.")
    lines.append("")

    lines.append("### Per-KE curated-triple recall")
    header = ["KE", "group", "title", "n_curated"]
    for mk in model_keys:
        header.append(f"{_short(mk)} matched")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for ke_id in ke_ids:
        any_row = next((results[mk]["kec"]["per_ke"].get(ke_id)
                        for mk in model_keys
                        if ke_id in results[mk]["kec"].get("per_ke", {})), None)
        if any_row is None:
            continue
        row = [ke_id, ke_group_by_id.get(ke_id, ""), any_row["title"],
              str(any_row["n_curated"])]
        for mk in model_keys:
            r = results[mk]["kec"]["per_ke"].get(ke_id)
            row.append(str(r["n_matched"]) if r else "-")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    for mk in model_keys:
        kec = results[mk]["kec"]
        lines.append(f"**{_short(mk)} total recall:** {kec['n_matched_total']}/"
                     f"{kec['n_curated_total']} ({kec['recall']:.1%})"
                     if kec.get("recall") is not None else
                     f"**{_short(mk)} total recall:** n/a")
    lines.append("")

    lines.append("### Standardized-assay recovery (Figure 1B, 19 methods)")
    header = ["reference assay", "guideline", "KE group(s)"]
    for mk in model_keys:
        header.append(_short(mk))
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    any_assays = next((results[mk]["assays"].get("assays")
                       for mk in model_keys if results[mk]["assays"].get("assays")), [])
    for i, ref_row in enumerate(any_assays):
        row = [ref_row["reference_assay"], ref_row["guideline"] or "-",
              ", ".join(ref_row["relevant_ke_groups"])]
        for mk in model_keys:
            a = results[mk]["assays"].get("assays", [])
            recovered = a[i]["recovered"] if i < len(a) else False
            row.append("recovered" if recovered else "-")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    for mk in model_keys:
        assays = results[mk]["assays"]
        if assays.get("recovery_rate") is not None:
            lines.append(f"**{_short(mk)} total recovery:** {assays['n_recovered']}/"
                         f"{assays['n_reference_assays']} ({assays['recovery_rate']:.1%})")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"wrote markdown tables -> {path}")


if enabled:
    assert kec_products_dir and model_keys, (
        "params 'kec_products_dir' and 'model_keys' are required")

    reference = _load_json(Path(reference_path))
    ke_ids = _all_ke_ids(reference)
    logger.info(f"AOP {reference['aop_wiki_id']}: {len(ke_ids)} reference KEs, "
                f"{len(reference['standardized_assays'])} standardized assays "
                f"(source: {reference['reference']['citation_key']})")

    embedder = None
    if assays_products_dir:
        try:
            from tasks.kec_compare import Embedder
            embedder = Embedder(embedding_model)
        except Exception as e:
            logger.warning(f"embedder unavailable, exact/name-only matching: {e}")

    results = {}
    for model_key in model_keys:
        kec_scores = _score_kec(model_key, ke_ids)
        assay_scores = (_score_assays(model_key, ke_ids, reference, embedder)
                         if assays_products_dir else {})
        results[model_key] = {"kec": kec_scores, "assays": assay_scores}
        logger.info(
            f"{model_key}: KEC recall="
            f"{kec_scores.get('recall')} "
            f"({kec_scores.get('n_matched_total')}/{kec_scores.get('n_curated_total')}), "
            f"assay recovery={assay_scores.get('recovery_rate')} "
            f"({assay_scores.get('n_recovered')}/{assay_scores.get('n_reference_assays')})"
        )

    consensus_scores = _score_assays_consensus(ke_ids, reference, embedder)
    if consensus_scores:
        logger.info(
            f"panel consensus ({consensus_scores['n_panel_models']} models): "
            f"assay recovery={consensus_scores['recovery_rate']} "
            f"({consensus_scores['n_recovered']}/"
            f"{consensus_scores['n_reference_assays']})")

    out = {
        "reference": reference["reference"],
        "aop_wiki_id": reference["aop_wiki_id"],
        "consensus_assays": consensus_scores,
        "ke_ids": ke_ids,
        "results": results,
    }

    out_path = Path(product["json"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        import pandas as pd
        rows = []
        for model_key, r in results.items():
            rows.append({
                "model_key": model_key,
                "kec_recall": r["kec"].get("recall"),
                "kec_n_matched": r["kec"].get("n_matched_total"),
                "kec_n_curated": r["kec"].get("n_curated_total"),
                "assay_recovery_rate": r["assays"].get("recovery_rate"),
                "assay_n_recovered": r["assays"].get("n_recovered"),
                "assay_n_reference": r["assays"].get("n_reference_assays"),
            })
        pd.DataFrame(rows).to_csv(product["csv"], index=False)
    except Exception as e:
        logger.warning(f"summary CSV write failed: {e}")

    if "md" in product:
        _write_markdown_tables(Path(product["md"]), reference, ke_ids, model_keys, results)

    logger.info(f"wrote {out_path}")
else:
    logger.info("aop451_reference_check disabled — skipping")
