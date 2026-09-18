"""
validate_assay_iris.py — Ploomber task: validate assay ontology-term IRIs via OLS4.

GT-free grounding-quality signal: of the ontology IRIs the model proposed for its assays'
endpoints, what fraction actually resolve to a real term in OLS4? Hallucinated IRIs do not
resolve. Mirrors validate_kec_iris.py (results cached; one OLS call per distinct IRI).

Two IRI populations are scored SEPARATELY and never mixed:
  ontology_terms — the assay's endpoint terms  → iri_valid_frac      (unchanged semantics)
  kec_triple     — the assay's KEC triple slots → kec_iri_valid_frac  (opt out with
                   include_kec_triple=False)
They are kept apart on purpose: `iri_valid_frac` is a live SMAA criterion, so folding a
new IRI population into it would silently shift model rankings and make runs
incomparable. `kec_iri_valid_frac` is a separate criterion a ranking config opts into
(see ranking_config in env.assays_consensus.yaml).

Ploomber:
  upstream: ["extract_assays"]
  product:  {"json": summary, "tsv": per-IRI (with a `source` column per population)}
  params:   include_kec_triple, OLS_TIMEOUT, OLS_RETRIES, OLS_THREADS
"""
from __future__ import annotations

import csv
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["extract_assays"]
product = None
enabled = True
model_key = None
include_kec_triple = True   # also score the kec_triple slot IRIs (separate metric)
OLS_URL = "https://www.ebi.ac.uk/ols4/api/terms"
OLS_TIMEOUT = 15
OLS_RETRIES = 3
OLS_THREADS = 4
# -

logger = setup_logging(Path(product["json"]).parent / "validate_assay_iris.log")

_NULL = {"null", "none", "n/a", "na", ""}
_ONTOLOGY_MAP = {
    "BAO": "bao", "CHMO": "chmo", "OBI": "obi", "GO": "go", "EFO": "efo",
    "FIX": "fix", "CL": "cl", "UBERON": "uberon", "NCBITAXON": "ncbitaxon",
    "PATO": "pato", "MMO": "mmo",
}


def _ols_check(iri: str, ontology: str | None) -> str:
    params = {"iri": iri}
    if ontology and ontology.lower() in _ONTOLOGY_MAP.values():
        params["ontology"] = ontology.lower()
    elif ontology and ontology.upper() in _ONTOLOGY_MAP:
        params["ontology"] = _ONTOLOGY_MAP[ontology.upper()]
    sess = requests.Session()
    sess.headers.update({"Accept": "application/json"})
    for attempt in range(OLS_RETRIES):
        try:
            r = sess.get(OLS_URL, params=params, timeout=OLS_TIMEOUT)
            if r.status_code == 200:
                n = r.json().get("page", {}).get("totalElements", 0)
                return "valid" if n > 0 else "not_found"
            if r.status_code in (429, 403):
                time.sleep(2 ** (attempt + 1))
                continue
            return f"http_{r.status_code}"
        except requests.exceptions.Timeout:
            time.sleep(2)
        except Exception as e:
            return f"error:{str(e)[:50]}"
    return "timeout"


if enabled:
    OUT_JSON = Path(product["json"])
    OUT_TSV = Path(product["tsv"])
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    with open(upstream["extract_assays"]["json"], encoding="utf-8") as f:
        assays_by_ke = json.load(f)

    def _add(counter, term) -> None:
        """Count one term's IRI, if it carries a usable one.
        `ontology` is the pre-AnnotatedTerm key, still in older assays.json."""
        iri = ((term or {}).get("iri") or "").strip()
        if iri and iri.lower() not in _NULL:
            counter[(iri, term.get("source") or term.get("ontology") or "")] += 1

    # two populations, counted separately: (iri, ontology) -> n occurrences
    occ = Counter()          # ontology_terms  → iri_valid_frac
    kec_occ = Counter()      # kec_triple slots → kec_iri_valid_frac
    for assays in assays_by_ke.values():
        for rec in (assays or []):
            for t in (rec.get("ontology_terms") or []):
                _add(occ, t)
            if include_kec_triple:
                for term in (rec.get("kec_triple") or {}).values():
                    _add(kec_occ, term)

    # one OLS call per distinct IRI across BOTH populations (shared result cache)
    distinct = sorted(set(occ) | set(kec_occ))
    logger.info(f"{model_key}: {len(distinct)} distinct IRIs "
                f"({len(occ)} ontology_terms, {len(kec_occ)} kec_triple), "
                f"{sum(occ.values()) + sum(kec_occ.values())} occurrences")

    status: dict = {}
    if distinct:
        with ThreadPoolExecutor(max_workers=OLS_THREADS) as pool:
            futs = {pool.submit(_ols_check, iri, ont): (iri, ont)
                    for (iri, ont) in distinct}
            for fut in as_completed(futs):
                key = futs[fut]
                status[key] = fut.result()

    with open(OUT_TSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["source", "iri", "ontology", "occurrences", "ols_status"])
        for src, counter in (("ontology_terms", occ), ("kec_triple", kec_occ)):
            for (iri, ont), n in sorted(counter.items()):
                w.writerow([src, iri, ont, n, status.get((iri, ont), "unchecked")])

    def _stats(counter) -> dict:
        """Validity stats for one IRI population (occurrence-weighted + distinct)."""
        n_distinct = len(counter)
        n_valid_distinct = sum(1 for k in counter if status.get(k) == "valid")
        n_occ = sum(counter.values())
        n_valid_occ = sum(n for k, n in counter.items() if status.get(k) == "valid")
        return {
            "n_distinct": n_distinct, "n_valid_distinct": n_valid_distinct,
            "n_occurrences": n_occ, "n_valid_occurrences": n_valid_occ,
            # occurrence-weighted validity = grounding quality criterion
            "valid_frac": (n_valid_occ / n_occ) if n_occ else None,
            "valid_frac_distinct": ((n_valid_distinct / n_distinct)
                                    if n_distinct else None),
            "status_counts": dict(Counter(status.get(k, "unchecked") for k in counter)),
        }

    s_ont, s_kec = _stats(occ), _stats(kec_occ)
    summary = {
        "model_key": model_key,
        # ── ontology_terms population (existing keys, unchanged meaning) ──
        "n_iris_distinct": s_ont["n_distinct"],
        "n_valid_distinct": s_ont["n_valid_distinct"],
        "n_iri_occurrences": s_ont["n_occurrences"],
        "n_valid_occurrences": s_ont["n_valid_occurrences"],
        "iri_valid_frac": s_ont["valid_frac"],
        "iri_valid_frac_distinct": s_ont["valid_frac_distinct"],
        "status_counts": s_ont["status_counts"],
        # ── kec_triple population (separate criterion; never folded into the above) ──
        "kec_triple_checked": bool(include_kec_triple),
        "n_kec_iris_distinct": s_kec["n_distinct"],
        "n_kec_valid_distinct": s_kec["n_valid_distinct"],
        "n_kec_iri_occurrences": s_kec["n_occurrences"],
        "n_kec_valid_occurrences": s_kec["n_valid_occurrences"],
        "kec_iri_valid_frac": s_kec["valid_frac"],
        "kec_iri_valid_frac_distinct": s_kec["valid_frac_distinct"],
        "kec_status_counts": s_kec["status_counts"],
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    logger.info(f"{model_key}: iri_valid_frac={summary['iri_valid_frac']} "
                f"({s_ont['n_valid_occurrences']}/{s_ont['n_occurrences']} occ), "
                f"kec_iri_valid_frac={summary['kec_iri_valid_frac']} "
                f"({s_kec['n_valid_occurrences']}/{s_kec['n_occurrences']} occ)")
else:
    logger.info("validate_assay_iris disabled — skipping")
