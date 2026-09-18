"""
ground_kec.py — Ploomber task: ground KEC object/process names to ontology IRIs via
the IdeaConsult dictionary search (tasks/ontology_ground.py).

Most LLM-extracted object/process terms have iri=null (the extractor is told never to
invent IRIs). This task grounds them by name against the ~30-ontology dictionary,
filling a suggested IRI and a grounding confidence for EVERY term — independent of
whether the model produced an IRI. It is also a trust signal: a name that grounds
'exact' is a real ontology term; 'fuzzy' is a review suggestion; 'none' is a red flag.

Action slots are skipped (controlled vocabulary, grounded differently).

Ontology preference: the dictionary's `ontology` field is a collection label, and the
available collections are dictionary-specific — so the preferred order is a parameter
you set (`preferred_ontologies`); ties fall back to Solr relevance.

Ploomber:
  upstream: ["extract_kec"]
  product:  {"json": kec_grounded.json, "summary": ..., "tsv": ...}
  params:   dictionary_url, preferred_ontologies, rows, timeout, threads, limit
"""
from __future__ import annotations

import csv
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tasks.kec_compare import _norm_name, llm_triples
from tasks.ontology_ground import ground, DEFAULT_DICT_URL
from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["extract_kec", "aopwiki_ontology_index"]
product = None
enabled = True
dictionary_url = DEFAULT_DICT_URL
preferred_ontologies = []      # collection labels, in preference order (you set this)
rows = 10
timeout = 20.0
threads = 8
limit = None                   # cap KEs (quick run)
# -

KEC_PATH = upstream["extract_kec"]["json"]
AOPWIKI_INDEX = upstream["aopwiki_ontology_index"]["json"]
OUT_JSON = Path(product["json"])
logger = setup_logging(OUT_JSON.parent / "ground_kec.log")

_SLOTS = ("biological_object", "biological_process")   # action is skipped


def _terms(kec: dict, kes):
    """Yield (term_dict, name) for every object/process term across the given KEs."""
    for kid in kes:
        for tr in llm_triples(kec.get(kid, {})):
            for slot in _SLOTS:
                term = tr.get(slot)
                if term and term.get("name"):
                    yield term, term["name"]


if enabled:
    OUT_SUMMARY = Path(product["summary"])
    OUT_TSV = Path(product["tsv"])
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    with open(KEC_PATH, encoding="utf-8") as f:
        kec = json.load(f)

    # AOP-Wiki's own terminology (checked first — consistent, curator-preferred)
    with open(AOPWIKI_INDEX, encoding="utf-8") as f:
        aopwiki_terms = json.load(f).get("terms", {})
    logger.info(f"AOP-Wiki index: {len(aopwiki_terms)} terms")

    kes = list(kec.keys())
    if limit:
        kes = kes[:limit]

    # unique names (dedup by normalised form) → one lookup each
    uniq = {}
    for _term, name in _terms(kec, kes):
        uniq.setdefault(_norm_name(name), name)
    logger.info(f"Grounding {len(uniq)} unique object/process names "
                f"(threads={threads}, preferred={preferred_ontologies or 'Solr-order'})")

    def _do(nm):
        key = _norm_name(nm)
        # 1. AOP-Wiki's own term (must carry an IRI — skips WIKI action stubs)
        t = aopwiki_terms.get(key)
        if t and t.get("iri"):
            return key, {"match": "aopwiki", "suggested_iri": t["iri"],
                         "ontology": t.get("source"), "suggested_name": t.get("name"),
                         "n_found": 1}
        # 2. external dictionary fallback
        try:
            return key, ground(nm, url=dictionary_url, rows=rows, timeout=timeout,
                               preferred=preferred_ontologies or None)
        except Exception as e:
            logger.warning(f"ground '{nm}' failed: {e}")
            return key, {"match": "error", "suggested_iri": None, "ontology": None,
                         "suggested_name": None, "n_found": 0}

    grounded_by_name = {}
    with ThreadPoolExecutor(max_workers=int(threads or 1)) as pool:
        for norm, g in pool.map(_do, uniq.values()):
            grounded_by_name[norm] = g

    # attach grounding to every term (non-destructive: model iri kept; suggestion added)
    _cats = ["aopwiki", "exact", "fuzzy", "none", "error"]
    counts = {s: {c: 0 for c in _cats} for s in ("object", "process")}
    filled_nulls = 0
    for kid in kes:
        for tr in llm_triples(kec.get(kid, {})):
            for slot in _SLOTS:
                term = tr.get(slot)
                if not (term and term.get("name")):
                    continue
                g = grounded_by_name.get(_norm_name(term["name"]))
                if not g:
                    continue
                term["grounding"] = g
                short = slot.replace("biological_", "")
                counts[short][g["match"]] = counts[short].get(g["match"], 0) + 1
                # an IRI we can offer where the model gave none
                if not term.get("iri") and g["match"] in ("aopwiki", "exact"):
                    filled_nulls += 1

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(kec, f, indent=2, ensure_ascii=False)

    # flat TSV (one row per grounded term)
    with open(OUT_TSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["ke_id", "slot", "name", "model_iri", "match", "ontology",
                    "suggested_name", "suggested_iri", "n_found"])
        for kid in kes:
            for tr in llm_triples(kec.get(kid, {})):
                for slot in _SLOTS:
                    term = tr.get(slot)
                    if not (term and term.get("name") and term.get("grounding")):
                        continue
                    g = term["grounding"]
                    w.writerow([kid, slot.replace("biological_", ""), term["name"],
                                term.get("iri"), g["match"], g["ontology"],
                                g["suggested_name"], g["suggested_iri"], g["n_found"]])

    summary = {
        "n_ke": len(kes),
        "n_unique_names": len(uniq),
        "per_slot": counts,
        "null_iris_filled_exact": filled_nulls,
        "dictionary_url": dictionary_url,
        "preferred_ontologies": list(preferred_ontologies),
    }
    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info(f"object: {counts['object']}  process: {counts['process']}")
    logger.info(f"null IRIs filled by exact grounding: {filled_nulls}")

    # per-model assessment figure (grounding outcomes per slot)
    try:
        from tasks.figviz import grounding_bar
        info = grounding_bar(counts, model=OUT_JSON.parent.name,
                             out_path=OUT_JSON.parent / "fig_grounding.png")
        logger.info(f"grounding figure → {info['out']}")
    except Exception as e:
        logger.warning(f"grounding figure skipped: {e}")
else:
    logger.info("ground_kec disabled — skipping")
