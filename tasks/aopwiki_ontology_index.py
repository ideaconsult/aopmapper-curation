"""
aopwiki_ontology_index.py — Ploomber task: index the ontology terms AOP-Wiki ALREADY uses.

AOP-Wiki's curated KEC terms (biological object/process/action), taxonomy, and organ/cell
terms reference ontology IRIs. This task catalogs them once per AOP-Wiki release into a
cached index, so the grounding step (tasks/ground_kec.py) can prefer AOP-Wiki's own
terminology before reaching for an external dictionary — more consistent and more
curator-acceptable.

IMPORTANT CAVEAT (per personal communication): AOP-Wiki's ontology annotations have
reportedly not been updated in ~17 years. So this index is a *terminology/consistency*
reference, NOT a guarantee of current IRIs — IRI currency must still be validated against
a live service (validate_kec_iris / OLS). This caveat is recorded in the output.

Version-level (depends only on aop.json), so the product is shared across models.

Ploomber:
  upstream: ["parse_pydantic"]
  product:  {"json": index, "summary": ontology counts}
"""
from __future__ import annotations

import json
from pathlib import Path

from tasks.ontology_ground import build_aopwiki_index
from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["parse_pydantic"]
product = None
enabled = True
# -

OUT_JSON = Path(product["json"])
logger = setup_logging(OUT_JSON.parent / "aopwiki_ontology_index.log")

STALE_NOTE = ("AOP-Wiki ontology annotations may be stale (reportedly not updated in "
              "~17 years). Use as a terminology/consistency reference; validate IRI "
              "currency against a live service (OLS) before relying on it.")


def _records(items, slot):
    for it in items:
        yield (slot, getattr(it, "name", None),
               getattr(it, "source", None), getattr(it, "source_id", None))


if enabled:
    from aopkb import Data
    from tasks.utils import preprocess_timestamps

    OUT_SUMMARY = Path(product["summary"])
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    with open(upstream["parse_pydantic"]["json"], encoding="utf-8") as f:
        raw = json.load(f)
    preprocess_timestamps(raw)
    data = Data.model_validate(raw)

    records = []
    records += list(_records(data.biological_object, "object"))
    records += list(_records(data.biological_process, "process"))
    records += list(_records(data.biological_action, "action"))
    records += list(_records(data.taxonomy, "taxonomy"))
    # organ / cell terms live on individual Key Events
    for ke in data.key_event:
        for attr, slot in (("organ_term", "organ"), ("cell_term", "cell")):
            t = getattr(ke, attr, None)
            if t and getattr(t, "name", None):
                records.append((slot, t.name, getattr(t, "source", None),
                                getattr(t, "source_id", None)))

    index = build_aopwiki_index(records)
    index["note"] = STALE_NOTE
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    # human-readable summary (which ontologies AOP-Wiki uses, per slot)
    summary = {
        "n_unique_terms": len(index["terms"]),
        "n_terms_per_slot": index["n_terms"],
        "ontologies_per_slot": index["ontologies"],
        "note": STALE_NOTE,
    }
    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    for slot, counts in index["ontologies"].items():
        top = dict(sorted(counts.items(), key=lambda x: -x[1])[:8])
        logger.info(f"{slot:<9} {sum(counts.values()):>4} terms | {top}")
    logger.info(f"unique terms (by name): {len(index['terms'])}")

    try:
        from tasks.figviz import ontology_usage_bar
        info = ontology_usage_bar(index["ontologies"],
                                  out_path=OUT_JSON.parent / "fig_aopwiki_ontologies.png")
        logger.info(f"ontology-usage figure → {info['out']}")
    except Exception as e:
        logger.warning(f"ontology-usage figure skipped: {e}")
else:
    logger.info("aopwiki_ontology_index disabled — skipping")
