"""
validate_kec_iris.py

Validate all ontology IRIs in kec.json llm_extracted terms via OLS4 API.
Results cached to kec_iri_validation.tsv so re-runs skip already-checked IRIs.

Run from aop4enm:
    python validate_kec_iris.py

To revalidate specific prefixes only (e.g. after IRI fix):
    python validate_kec_iris.py --recheck RO BAO MO NCBITAXON HGNC
"""

import json
import re
import time
import csv
import threading
from collections import defaultdict
from pathlib import Path

import requests


# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["extract_kec"]
product = None
enabled = None
OLS_URL = "https://www.ebi.ac.uk/ols4/api/terms"
OLS_DELAY    = 0.2    # seconds between requests per thread — conservative after 403s
OLS_TIMEOUT  = 15
OLS_RETRIES  = 3
OLS_THREADS  = 4      # parallel OLS requests; keep low to avoid rate-limiting
recheck = []
# -


KEC_PATH = upstream["extract_kec"]["json"]
OUT_TSV  = Path(product["data"])


OLS_ONTOLOGY_MAP = {
    "GO":        "go",
    "CL":        "cl",
    "CHEBI":     "chebi",
    "UBERON":    "uberon",
    "PR":        "pr",
    "MP":        "mp",
    "HP":        "hp",
    "DOID":      "doid",
    "RO":        "ro",
    "BAO":       "bao",
    "MONDO":     "mondo",
    "FMA":       "fma",
    "PATO":      "pato",
    "NCBITAXON": "ncbitaxon",
    "NCBIGENE":  "ncbigene",
    "HGNC":      "hgnc",
    "UO":        "uo",
    "MO":        "mo",
}


def build_iri(prefix: str, local_id: str) -> str | None:
    """Reconstruct the canonical IRI for an OLS lookup."""
    if prefix in ("NULL", "UNKNOWN", "WIKI_ACTION"):
        return None
    if prefix == "MESH":
        return f"http://id.nlm.nih.gov/mesh/{local_id}"
    if prefix == "FMA":
        return f"http://purl.org/sig/ont/fma/fma{local_id}"
    if prefix == "BAO":
        # BAO uses its own namespace, not OBO Foundry purl
        return f"http://www.bioassayontology.org/bao#BAO_{local_id}"
    if prefix == "NCBITAXON":
        # OLS4 stores NCBITaxon with capital T in the IRI
        return f"http://purl.obolibrary.org/obo/NCBITaxon_{local_id}"
    if prefix == "HGNC":
        # OLS4 indexes HGNC via OBO-style purl
        return f"http://purl.obolibrary.org/obo/HGNC_{local_id}"
    if prefix == "NCBIGENE":
        return f"http://purl.obolibrary.org/obo/NCBIGene_{local_id}"
    # Default: OBO Foundry purl
    return f"http://purl.obolibrary.org/obo/{prefix}_{local_id}"


# ── IRI parsing ───────────────────────────────────────────────────────────────

_NULL_STRINGS = {"null", "none", "n/a", "na", ""}


def parse_iri(iri: str) -> tuple[str, str]:
    if not iri or str(iri).lower() in _NULL_STRINGS:
        return ("NULL", "")
    iri = str(iri).strip()
    if re.match(r"^\d+$", iri):
        return ("WIKI_ACTION", iri)
    m = re.match(r"https?://purl\.obolibrary\.org/obo/([A-Za-z]+)_(\S+)", iri)
    if m:
        return (m.group(1).upper(), m.group(2))
    if "nlm.nih.gov/mesh/" in iri:
        return ("MESH", iri.split("/")[-1])
    if "purl.org/sig/ont/fma" in iri:
        local = re.sub(r".*fma/?", "", iri, flags=re.IGNORECASE)
        return ("FMA", local)
    if "genenames.org" in iri:
        local = re.search(r"HGNC:(\d+)", iri)
        return ("HGNC", local.group(1) if local else iri)
    if "bioassayontology.org" in iri:
        local = re.search(r"BAO_(\d+)", iri)
        return ("BAO", local.group(1) if local else iri)
    return ("UNKNOWN", iri)


# ── OLS validation ────────────────────────────────────────────────────────────

def ols_check(iri_full: str, ontology: str | None, enabled: bool = True) -> str:
    if not enabled:
        return f"not checked (enabled {enabled})"
    params = {"iri": iri_full}
    if ontology:
        params["ontology"] = ontology

    sess = requests.Session()
    sess.headers.update({"Accept": "application/json"})
    for attempt in range(OLS_RETRIES):
        try:
            r = sess.get(OLS_URL, params=params, timeout=OLS_TIMEOUT)
            if r.status_code == 200:
                n = r.json().get("page", {}).get("totalElements", 0)
                return "valid" if n > 0 else "not_found"
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"    rate-limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code == 403:
                print(f"    403 forbidden, waiting 30s...")
                time.sleep(30)
                continue
            return f"http_{r.status_code}"
        except requests.exceptions.Timeout:
            time.sleep(2)
        except Exception as e:
            return f"error:{str(e)[:60]}"
    return "timeout"


# ── helpers ───────────────────────────────────────────────────────────────────

def load_existing_results(path: Path) -> dict:
    results = {}
    if not path.exists():
        return results
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row.get("ols_status") not in ("", None):
                results[row["iri"]] = row
    print(f"Loaded {len(results)} cached results from {path}")
    return results


def load_ols_cache(path: Path) -> dict:
    """Shared cross-model IRI→status cache."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        print(f"Loaded {len(data)} entries from shared OLS cache {path}")
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def save_ols_cache(path: Path, cache: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=None, separators=(",", ":"))



def _iter_terms(llm: dict):
    """Yield (slot, term_dict) for every ontology-bearing term in an
    llm_extracted block.

    Supports the current nested-triple schema
        llm_extracted = {"triples": [{"biological_object": {..},
                                       "biological_process": {..},
                                       "biological_action": {..}}, ..],
                         "organ_terms": [..], "cell_terms": [..]}
    and the legacy flat-list schema (biological_objects/processes/actions at
    the top level) so that older product files still validate correctly.
    """
    # Current schema: nested slots inside each triple
    for t in llm.get("triples", []) or []:
        if not isinstance(t, dict):
            continue
        for slot, key in (
            ("object",  "biological_object"),
            ("process", "biological_process"),
            ("action",  "biological_action"),
        ):
            term = t.get(key)
            if isinstance(term, dict):
                yield slot, term
    # Legacy schema: flat top-level lists (kept for backward compatibility)
    for slot, key in (
        ("object",  "biological_objects"),
        ("process", "biological_processes"),
        ("action",  "biological_actions"),
    ):
        for term in llm.get(key, []) or []:
            if isinstance(term, dict):
                yield slot, term
    # Organ / cell term lists (flat in both schemas)
    for slot, key in (
        ("organ", "organ_terms"),
        ("cell",  "cell_terms"),
    ):
        for term in llm.get(key, []) or []:
            if isinstance(term, dict):
                yield slot, term


def collect_iris(kec: dict) -> dict:
    iri_records = {}
    for ke_id, rec in kec.items():
        llm = rec.get("llm_extracted")
        if not llm:
            continue
        for slot, t in _iter_terms(llm):
            iri = str(t.get("iri", "") or "").strip()
            if not iri or iri.lower() in _NULL_STRINGS:
                continue
            if iri not in iri_records:
                prefix, local_id = parse_iri(iri)
                iri_records[iri] = {
                    "iri":        iri,
                    "name":       t.get("name", ""),
                    "prefix":     prefix,
                    "local_id":   local_id,
                    "solr_id":    f"{prefix}_{local_id}" if prefix not in ("NULL","UNKNOWN","WIKI_ACTION") else local_id,
                    "slot_types": set(),
                    "ke_count":   0,
                    "ols_status": None,
                }
            iri_records[iri]["slot_types"].add(slot)
            iri_records[iri]["ke_count"] += 1
    return iri_records


def print_summary(iri_records: dict):
    prefix_stats = defaultdict(lambda: defaultdict(int))
    for rec in iri_records.values():
        prefix_stats[rec["prefix"]][rec["ols_status"]] += 1

    skipped_prefixes = {"NULL", "WIKI_ACTION", "UNKNOWN"}
    total_valid = total_nf = total_other = 0
    grand = 0

    print(f"\n{'='*70}")
    print(f"{'Prefix':<12} {'Total':>7}  {'valid':>7}  {'not_found':>10}  {'other':>7}")
    print("-"*55)
    for prefix in sorted(prefix_stats, key=lambda p: -sum(prefix_stats[p].values())):
        stats = prefix_stats[prefix]
        total = sum(stats.values())
        valid = stats.get("valid", 0)
        nf    = stats.get("not_found", 0)
        other = total - valid - nf
        if prefix not in skipped_prefixes:
            total_valid += valid
            total_nf    += nf
            total_other += other
            grand       += total
        other_detail = "  ".join(f"{k}:{v}" for k, v in stats.items()
                                  if k not in ("valid", "not_found"))
        print(f"  {prefix:<10} {total:>7}  {valid:>7}  {nf:>10}  {other:>7}  {other_detail}")

    print("-"*55)
    all_total = len(iri_records)
    print(f"  {'TOTAL':<10} {all_total:>7}  {total_valid:>7}  {total_nf:>10}  {total_other:>7}")
    pct = 100 * total_valid / grand if grand else 0
    print(f"\n  Valid: {pct:.1f}% of validated IRIs  ({total_nf} not found, {total_other} errors/skipped)")


def write_outputs(iri_records: dict):
    print(f"\nWriting {OUT_TSV}")
    with open(OUT_TSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["iri","name","prefix","local_id","solr_id",
                          "slot_types","ke_count","ols_status"])
        for iri, rec in sorted(iri_records.items(),
                                key=lambda x: (x[1]["prefix"], x[1]["local_id"])):
            writer.writerow([
                iri, rec["name"], rec["prefix"], rec["local_id"], rec["solr_id"],
                "|".join(sorted(rec["slot_types"])), rec["ke_count"],
                rec["ols_status"] or "",
            ])

    nf_path = OUT_TSV.with_name("kec_iri_not_found.tsv")
    with open(nf_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["iri","name","prefix","solr_id","slot_types","ke_count"])
        for iri, rec in sorted(iri_records.items(),
                                key=lambda x: (x[1]["prefix"], x[1]["local_id"])):
            if rec["ols_status"] == "not_found":
                writer.writerow([
                    iri, rec["name"], rec["prefix"], rec["solr_id"],
                    "|".join(sorted(rec["slot_types"])), rec["ke_count"],
                ])
    print(f"Not-found IRIs → {nf_path}")


# ── main ──────────────────────────────────────────────────────────────────────

def main(enabled, recheck):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    recheck_prefixes = {p.upper() for p in recheck} if recheck is not None else None

    print(f"Loading {KEC_PATH}")
    with open(KEC_PATH, encoding="utf-8") as f:
        kec = json.load(f)

    iri_records = collect_iris(kec)
    print(f"Unique IRIs: {len(iri_records)}")

    # Shared cross-model cache: IRI → ols_status
    ols_cache_path = OUT_TSV.parents[1] / "ols_cache.json"
    ols_cache = load_ols_cache(ols_cache_path)
    cache_lock = threading.Lock()

    # Apply per-model TSV cache first (has slot_type / ke_count context)
    cached = load_existing_results(OUT_TSV)
    for iri, cached_row in cached.items():
        if iri not in iri_records:
            continue
        prefix = iri_records[iri]["prefix"]
        if recheck_prefixes is None or prefix in recheck_prefixes:
            continue
        iri_records[iri]["ols_status"] = cached_row["ols_status"]

    # Apply shared OLS cache for any still-unresolved IRIs
    shared_hits = 0
    for iri, rec in iri_records.items():
        if not rec["ols_status"] and iri in ols_cache:
            prefix = rec["prefix"]
            if recheck_prefixes is None or prefix not in recheck_prefixes:
                rec["ols_status"] = ols_cache[iri]
                shared_hits += 1
    if shared_hits:
        print(f"Resolved {shared_hits} IRIs from shared OLS cache")

    # Mark skip-only prefixes
    skip_prefixes = {"NULL", "WIKI_ACTION", "UNKNOWN"}
    for rec in iri_records.values():
        if rec["prefix"] in skip_prefixes and not rec["ols_status"]:
            rec["ols_status"] = rec["prefix"].lower()

    to_validate = [rec for rec in iri_records.values() if not rec["ols_status"]]
    print(f"To validate: {len(to_validate)}  |  Cached/skipped: {len(iri_records)-len(to_validate)}")
    if recheck_prefixes:
        print(f"Rechecking prefixes: {sorted(recheck_prefixes)}")

    n = len(to_validate)
    counters = {"ok": 0, "nf": 0, "errors": 0, "done": 0}
    counters_lock = threading.Lock()

    def _validate(rec):
        iri_full = build_iri(rec["prefix"], rec["local_id"])
        if iri_full is None:
            return rec, "no_iri"
        if enabled:
            time.sleep(OLS_DELAY)
        ontology = OLS_ONTOLOGY_MAP.get(rec["prefix"])
        status = ols_check(iri_full, ontology, enabled=enabled)
        return rec, status

    cache_dirty = False
    with ThreadPoolExecutor(max_workers=OLS_THREADS) as pool:
        futures = {pool.submit(_validate, rec): rec for rec in to_validate}
        for future in as_completed(futures):
            rec, status = future.result()
            rec["ols_status"] = status

            with cache_lock:
                ols_cache[rec["iri"]] = status
                cache_dirty = True

            with counters_lock:
                if status == "valid":       counters["ok"] += 1
                elif status == "not_found": counters["nf"] += 1
                else:                       counters["errors"] += 1
                counters["done"] += 1
                i = counters["done"]

            if i % 50 == 0 or i == n:
                print(f"  [{i}/{n}]  valid={counters['ok']}  not_found={counters['nf']}  errors={counters['errors']}")
                if cache_dirty:
                    with cache_lock:
                        save_ols_cache(ols_cache_path, ols_cache)
                    cache_dirty = False

    if cache_dirty:
        save_ols_cache(ols_cache_path, ols_cache)

    print_summary(iri_records)
    write_outputs(iri_records)
    print("Done.")


if __name__ == "__main__":
    main(enabled=enabled, recheck=recheck)
