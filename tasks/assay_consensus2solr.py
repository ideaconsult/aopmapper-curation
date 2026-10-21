"""
assay_consensus2solr.py — Ploomber task: structured assay Solr docs from cross-model
assay consensus. REPLACES the name-only tasks/assay2solr.py in the Solr spine.

Emits two entity types, so a technique described in many Key Events is stored once and
linked many times rather than duplicated per KE:
  * `assay_technique` — one per consolidated assay-name group;
  * `ke_assay`        — one per (KE, consensus assay), carrying the AssayRecord fields.

The name-consolidation logic (punct-normalise -> synonym map -> group key -> hashed
technique id, with IRI frequency filtering) is carried over from the now-obsolete
tasks/assay2solr_rich.py, which did the same job against a remote `aop_assays` Solr
collection. Sourcing from the assay pipeline's own consensus removes that Solr
round-trip and the sync_assays_solr prerequisite entirely.

Inputs:
  - upstream["ke_graph"]["lookup"]: current release's uuid -> KEnnn (get_kid), the
    authoritative KE set. Docs are emitted only for KEs present here, so a retired KE
    can never leave an orphan ke_assay/assay_technique behind.
  - viz_assay_consensus_json (BY PATH — product of pipeline.assays_consensus.yaml):
    per-KE consensus clusters, already keyed by KEnnn, each with a representative
    AssayRecord (`assay`), `support` and `models`.
  - assays_products (BY PATH): root holding per-model
    assays/{{model_key}}/validate_assay_iris.tsv, for the OLS-valid ontology-id set.
  - upstream["assay_synonyms_update"]["synonyms"]: the name-consolidation map, maintained by
    tasks/assay_synonyms_update.py (param assay_synonyms is the fallback when run standalone).

Ontology ids are written in AOP-Wiki's short form (GO_0006954) so they join against the
wiki-derived ontology docs, and only when OLS resolved them (hard gate, both tracks).

Ploomber:
  upstream: ["ke_graph", "assay_synonyms_update"]
  product:  {"nb", "json", "assays_ke_cleaned", "stats", "xml_records", "assay_ids",
            "technique_ids"}
  params:   viz_assay_consensus_json, assays_products, assay_min_support,
            assay_synonyms, iri_min_freq, model_version

`assay_ids` is the permanent-number registry resources/assay_ids.json {assay identity -> n}: a
ke_assay doc id is ASSAY<n> (like the wiki's KE1234), assigned once per (KE, normalised name,
normalised endpoint) from an append-only registry (never removed, a number never goes to a
different identity); new identities get max+1. The id does not
depend on the technique group. Records that share an identity share an id and are counted
as `n_ke_assay_id_collisions` in the stats product.

`technique_ids` is the same for techniques (resources/technique_ids.json, {group key -> n}, doc
id TECH<n>; append-only). The group key is the canonical name after synonym resolution. A
key without a number gets max+1; a key merged into another keeps its entry, so an undone
merge restores its old number. An id changes only through an explicit merge: the canonical
keeps (or, if new, gets) its number and each absorbed alias number is reported - WARNING
plus `retired_technique_ids` {absorbed id -> surviving id} in the stats product (references
to absorbed ids go stale by necessity).

`xml_records` is {"records": [...], "techniques": [...]}: the filtered per-(KE, assay)
records (with ontology labels, the Solr ids `id`/`technique_id` and the KE's AOP-Wiki UUID
`ke_uuid`) and the assay techniques (id, name, synonyms), consumed by
tasks/export_assays_xml.py.

`stats` carries the technique-vs-record counts and the most-reused techniques (each with
the distinct endpoints it measures). Those two counts only exist together here, after
name consolidation — the crossmodel stage counts per-KE assay records and has no notion
of a technique — so this product is what pipeline.manuscript.yaml reads rather than
re-deriving the grouping. Note both counts depend on assay_min_support, since
consolidation runs after that gate.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from tasks.assay_names import (assay_identity, assign_ids, assign_technique_ids,
                                build_synonym_map, load_id_registry, resolve,
                                technique_id, write_id_registry)
from tasks.assay_names import ke_assay_id as make_ke_assay_id
from tasks.kec_compare import iri_to_solr_id
from tasks.logutil import setup_logging
from tasks.utils import get_kid

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["ke_graph", "assay_synonyms_update"]
product = None
viz_assay_consensus_json: str = ""
assays_products: str = ""
assay_min_support: int = 2
assay_synonyms: str = "resources/assay_synonyms.json"
iri_min_freq: int = 2
model_version: str = "consensus"
# -

logger = setup_logging(Path(product["json"]).parent / "assay_consensus2solr.log")


# ── name consolidation: tasks/assay_names.py (punct-normalise -> synonym map -> key) ──


def _kec_triple_fields(kec_triple: Optional[dict]) -> dict:
    """One assay's own kec_triple (AssayRecord.kec_triple, tasks/assay_models.py) as
    flat Solr fields — the representative model's read of what biological change this
    assay measures, same status as every other field on this doc: one model's output,
    not a cross-model consensus. (Unlike the KEC track, where crossmodel_compare.py
    clusters triples themselves, nothing here clusters kec_triples across models — a
    TODO for a future pass, see assay_consensus2solr.py's stats-product docstring.)

    Object/process are IRI-gated to short form (GO_0006954, matching the KEC track's
    encoding); action is name-only, controlled vocabulary, no IRI. Absent slots are
    omitted rather than written null, so a doc with no kec_triple has none of these keys.
    """
    if not kec_triple:
        return {}
    obj = (kec_triple.get("biological_object") or {})
    proc = (kec_triple.get("biological_process") or {})
    act = (kec_triple.get("biological_action") or {})
    out: dict = {}
    if obj.get("name"):
        out["kec_object_t"] = obj["name"]
        sid = iri_to_solr_id(obj.get("iri"))
        if sid:
            out["kec_object_id_s"] = sid
    if proc.get("name"):
        out["kec_process_t"] = proc["name"]
        sid = iri_to_solr_id(proc.get("iri"))
        if sid:
            out["kec_process_id_s"] = sid
    if act.get("name"):
        out["kec_action_s"] = act["name"]
    return out


def _clean_display(name: str) -> str:
    return re.sub(r"\s+", " ", name.lower().strip())


# ── inputs ────────────────────────────────────────────────────────────────────

def _load_ols_valid_solr_ids(assays_products: str,
                             model_keys: list[str]) -> set[str]:
    """Short Solr ids ("GO_0006954") of the IRIs OLS resolved, over the panel's models.
    Read from the per-IRI validate_assay_iris.tsv (the .json sibling is summary only).

    Keyed by short id, not the raw IRI, because that is the form AOP-Wiki itself uses
    (source + source_id, cf. utils.build_wiki_id_lookup) and therefore the form every
    ontology reference already in the index is written in. The LLM emits full IRIs, so
    both sides go through iri_to_solr_id to meet."""
    valid: set[str] = set()
    for mk in model_keys:
        path = Path(assays_products) / "assays" / mk / "validate_assay_iris.tsv"
        if not path.exists():
            logger.warning(f"validate_assay_iris.tsv missing for {mk} — skipping")
            continue
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                if row.get("ols_status") == "valid":
                    sid = iri_to_solr_id(row.get("iri"))
                    if sid:
                        valid.add(sid)
    return valid


def clusters_to_records(viz: dict, current_ke_ids: set[str],
                        valid_solr_ids: set[str], min_support: int) -> list[dict]:
    """Consensus cluster representatives -> flat per-(KE, assay) records, with the
    ontology ids already gated and normalised to short form."""
    records: list[dict] = []
    n_skipped_ke = n_skipped_support = n_dropped_iri = 0

    for ke in viz.get("kes", []):
        ke_id = ke.get("ke_id")
        if ke_id not in current_ke_ids:
            n_skipped_ke += 1
            continue
        for cluster in ke.get("clusters", []):
            if cluster.get("support", 0) < min_support:
                n_skipped_support += 1
                continue
            rec = cluster.get("assay") or {}
            name = (rec.get("assay_name") or "").strip()
            if not name:
                continue

            iris: list[str] = []
            terms: list[dict] = []     # same terms as `iris`, with the label kept
            for term in rec.get("ontology_terms") or []:
                sid = iri_to_solr_id(term.get("iri"))
                if not sid:
                    continue
                if sid in valid_solr_ids:
                    if sid not in iris:
                        iris.append(sid)
                        terms.append({"name": (term.get("name") or "").strip(),
                                      "id": sid, "source": term.get("source")})
                else:
                    n_dropped_iri += 1

            records.append({
                "ke_id": ke_id,
                "assay_name": name,
                "description": rec.get("description") or "",
                "method_type": rec.get("method_type") or "not_specified",
                "measured_endpoint": rec.get("measured_endpoint") or "",
                "endpoint_direction": rec.get("endpoint_direction") or "not_specified",
                "ontology_ids": iris,
                # label-bearing twin of ontology_ids (the XML export needs the label)
                "ontology_terms": terms,
                "cell_types": rec.get("cell_types") or [],
                "organ_tissue": rec.get("organ_tissue") or [],
                "species": rec.get("species") or [],
                "guideline": rec.get("guideline") or "",
                "throughput": rec.get("throughput") or "not_specified",
                "evidence": rec.get("evidence") or [],
                "support": cluster.get("support", 0),
                "models": cluster.get("models") or [],
                # the representative's own read of what biological change this assay
                # measures (tasks/assay_models.py AssayRecord.kec_triple) — one model's
                # triple, not a panel consensus, same as every other field here
                "kec_triple": rec.get("kec_triple"),
            })

    logger.info(
        f"consensus -> records: {len(records)} kept; {n_skipped_ke} KEs not in the "
        f"current release, {n_skipped_support} clusters below support>={min_support}, "
        f"{n_dropped_iri} ontology ids dropped (not OLS-valid)"
    )
    return records


# ── document builder ──────────────────────────────────────────────────────────

def build_solr_docs(records: list[dict], model_version: str, iri_min_freq: int,
                    synonym_map: dict[str, str], assay_ids: dict[str, int],
                    technique_numbers: dict[str, int]) -> tuple[list[dict], dict]:
    """Pass 1 accumulates per-group-key evidence (ids, IRIs, surface forms, votes);
    pass 2 emits one assay_technique per group key and one ke_assay per record."""

    iri_freq: dict[str, Counter] = defaultdict(Counter)
    ke_ids: dict[str, set] = defaultdict(set)
    surface_forms: dict[str, set] = defaultdict(set)
    method_votes: dict[str, Counter] = defaultdict(Counter)
    tput_votes: dict[str, Counter] = defaultdict(Counter)
    canon_display: dict[str, str] = {}
    group_keys: list[str] = []

    for rec in records:
        gk = resolve(rec["assay_name"], synonym_map)
        group_keys.append(gk)
        name = rec["assay_name"]
        if gk not in canon_display or (
            name[:1].isupper() and not canon_display[gk][:1].isupper()
        ):
            canon_display[gk] = name
        surface_forms[gk].add(name)
        ke_ids[gk].add(rec["ke_id"])
        method_votes[gk][rec["method_type"]] += 1
        tput_votes[gk][rec["throughput"]] += 1
        for sid in rec["ontology_ids"]:
            iri_freq[gk][sid] += 1

    tech_by_gk: dict[str, dict] = {}
    for gk, ke_set in ke_ids.items():
        best_name = canon_display[gk]
        tech_by_gk[gk] = {
            "id": technique_id(technique_numbers[gk]),
            "type_s": "assay_technique",
            "name_t": best_name,
            "name_clean_t": _clean_display(best_name),
            "method_type_s": method_votes[gk].most_common(1)[0][0],
            "throughput_s": tput_votes[gk].most_common(1)[0][0],
            "source_t": "measurement_methodology_llm",
            "model_version_s": model_version,
            "ontology_iri_ss": sorted(
                sid for sid, n in iri_freq[gk].items() if n >= iri_min_freq),
            "ke_ids_ss": sorted(ke_set),
            "attr_synonyms": sorted(surface_forms[gk] - {best_name}),
            # graph edges (AOPMapper's network view builds edges purely from
            # upstream_ss/downstream_ss): a technique has no upstream, and is
            # downstream of every KE it was extracted from — this is what lets one
            # technique used across many KEs show up as a bridging node instead of an
            # isolated one.
            "upstream_ss": [],
            "downstream_ss": sorted(ke_set),
        }

    ke_assay_docs: list[dict] = []
    assays_ke_cleaned: dict[str, list] = defaultdict(list)
    for rec, gk in zip(records, group_keys):
        tech_id = tech_by_gk[gk]["id"]
        ke_assay_id = make_ke_assay_id(assay_ids, rec["ke_id"], rec["assay_name"],
                                       rec["measured_endpoint"])
        ke_assay_docs.append({
            "id": ke_assay_id,
            "type_s": "ke_assay",
            "ke_id_s": rec["ke_id"],
            "technique_id_s": tech_id,
            "name_t": rec["assay_name"],
            "name_clean_t": _clean_display(rec["assay_name"]),
            "description_t": rec["description"],
            "measured_endpoint_t": rec["measured_endpoint"],
            "endpoint_direction_s": rec["endpoint_direction"],
            "method_type_s": rec["method_type"],
            "cell_types_ss": rec["cell_types"],
            "organ_tissue_ss": rec["organ_tissue"],
            "species_ss": rec["species"],
            "guideline_s": rec["guideline"],
            "throughput_s": rec["throughput"],
            "ontology_iri_ss": rec["ontology_ids"],
            "evidence_ss": rec["evidence"],
            "source_t": "measurement_methodology_llm",
            "model_version_s": model_version,
            # provenance — mirrors the KEC track's per-triple _prov
            "provenance_s": "llm",
            "support_d": rec["support"],
            "models_ss": rec["models"],
            "upstream_ss": [tech_id],
            "downstream_ss": [rec["ke_id"]],
            **_kec_triple_fields(rec.get("kec_triple")),
        })
        assays_ke_cleaned[rec["ke_id"]].append({
            "id": ke_assay_id,
            "technique_id": tech_id,
            "name": rec["assay_name"],
            "description": rec["description"],
            "endpoint": rec["measured_endpoint"],
            "method_type": rec["method_type"],
        })

    return list(tech_by_gk.values()) + ke_assay_docs, dict(assays_ke_cleaned)


# ── task body ─────────────────────────────────────────────────────────────────

with open(upstream["ke_graph"]["lookup"], encoding="utf-8") as f:
    lookup = json.load(f)
current_ke_ids = {
    get_kid(lookup, uuid, "key-event-reference")
    for uuid in lookup.get("key-event-reference", {})
}
current_ke_ids.discard(None)
# KEnnn -> the Key Event's AOP-Wiki UUID, for the XML export (which links by UUID)
ke_uuid_by_id = {get_kid(lookup, uuid, "key-event-reference"): uuid
                 for uuid in lookup.get("key-event-reference", {})}
logger.info(f"current release: {len(current_ke_ids)} KEs")

with open(viz_assay_consensus_json, encoding="utf-8") as f:
    viz = json.load(f)
panel_models = (viz.get("summary") or {}).get("models") or []
logger.info(f"assay panel: {len(panel_models)} models")

valid_solr_ids = _load_ols_valid_solr_ids(assays_products, panel_models)
logger.info(f"OLS-valid assay ontology ids: {len(valid_solr_ids)}")

synonym_file = (upstream["assay_synonyms_update"]["synonyms"]
                if isinstance(upstream, dict) and "assay_synonyms_update" in upstream
                else assay_synonyms)
logger.info(f"synonym map: {synonym_file}")

records = clusters_to_records(
    viz, current_ke_ids, valid_solr_ids, int(assay_min_support))

# permanent ASSAY<n> numbers: existing assay identities keep theirs, new ones get max+1
assay_id_registry = load_id_registry(product["assay_ids"])
n_registry_before = len(assay_id_registry)
n_new_assay_ids = assign_ids(
    (assay_identity(r["ke_id"], r["assay_name"], r["measured_endpoint"]) for r in records),
    assay_id_registry)
write_id_registry(assay_id_registry, product["assay_ids"])
logger.info(f"assay id registry {product['assay_ids']}: {n_registry_before} -> "
            f"{len(assay_id_registry)} identities ({n_new_assay_ids} new)")

# permanent TECH<n> numbers, keyed by the group key (canonical name after synonym
# resolution); append-only registry, merges reported in retired_technique_ids
synonym_map = build_synonym_map(synonym_file)
technique_registry = load_id_registry(product["technique_ids"])
n_tech_registry_before = len(technique_registry)
technique_numbers, retired_technique_ids = assign_technique_ids(
    [resolve(r["assay_name"], synonym_map) for r in records],
    technique_registry, synonym_map)
retired_technique_ids = {technique_id(old): technique_id(new)
                         for old, new in sorted(retired_technique_ids.items())}
if retired_technique_ids:
    logger.warning(
        f"{len(retired_technique_ids)} technique ids absorbed by a synonym merge "
        f"(references to them go stale): "
        + ", ".join(f"{o} -> {n}" for o, n in retired_technique_ids.items()))
write_id_registry(technique_registry, product["technique_ids"])
logger.info(f"technique id registry {product['technique_ids']}: "
            f"{n_tech_registry_before} -> {len(technique_registry)} identities, "
            f"{len(set(technique_numbers.values()))} techniques in use")

solr_docs, assays_ke_cleaned = build_solr_docs(
    records=records,
    model_version=model_version,
    iri_min_freq=int(iri_min_freq),
    synonym_map=synonym_map,
    assay_ids=assay_id_registry,
    technique_numbers=technique_numbers,
)

out_json = Path(product["json"])
out_json.parent.mkdir(parents=True, exist_ok=True)
with open(out_json, "w", encoding="utf-8") as f:
    json.dump(solr_docs, f, ensure_ascii=False)

# The filtered, label-bearing records themselves (current-release KEs, OLS-valid ontology
# ids, support gate) — the exact input of the XML export (tasks/export_assays_xml.py), so
# the XML cannot drift from the curation filters applied above.
out_xml_records = Path(product["xml_records"])
out_xml_records.parent.mkdir(parents=True, exist_ok=True)
with open(out_xml_records, "w", encoding="utf-8") as f:
    # `id` / `technique_id` are the ke_assay / assay_technique doc ids (ASSAY<n>,
    # TECH<n>), so the XML lookup section and Solr share ids; build_solr_docs emits
    # exactly one ke_assay doc per record, in record order
    ke_assay_docs = [d for d in solr_docs if d["type_s"] == "ke_assay"]
    assert len(ke_assay_docs) == len(records)
    json.dump({
        "records": [{**r, "id": d["id"], "technique_id": d["technique_id_s"],
                     "ke_uuid": ke_uuid_by_id.get(r["ke_id"])}
                    for r, d in zip(records, ke_assay_docs)],
        "techniques": [{"id": d["id"], "name": d["name_t"],
                        "synonyms": d["attr_synonyms"]}
                       for d in solr_docs if d["type_s"] == "assay_technique"],
    }, f, ensure_ascii=False)

out_cleaned = Path(product["assays_ke_cleaned"])
out_cleaned.parent.mkdir(parents=True, exist_ok=True)
with open(out_cleaned, "w", encoding="utf-8") as f:
    json.dump(assays_ke_cleaned, f, ensure_ascii=False)

n_tech = sum(1 for d in solr_docs if d["type_s"] == "assay_technique")
n_ke_assay = sum(1 for d in solr_docs if d["type_s"] == "ke_assay")
# ke_assay ids are a function of the record (KE, normalised name, endpoint); records that agree
# on all three share an id and would overwrite each other in Solr — reported, not hidden
ke_assay_id_counts = Counter(d["id"] for d in solr_docs if d["type_s"] == "ke_assay")
n_ke_assay_id_collisions = sum(n - 1 for n in ke_assay_id_counts.values() if n > 1)
if n_ke_assay_id_collisions:
    logger.warning(f"{n_ke_assay_id_collisions} ke_assay records share an id with another "
                   f"record (same KE, normalised name and endpoint)")
logger.info(
    f"wrote {out_json}: {n_tech} assay_technique + {n_ke_assay} ke_assay docs "
    f"over {len(assays_ke_cleaned)} KEs"
)

# ── stats product ─────────────────────────────────────────────────────────────
# The technique/KE-record distinction is only made here, after name consolidation, so
# this is the only place the two counts exist together. Written as a declared product
# (not left in the log) so pipeline.manuscript.yaml can cite them: the manuscript
# reports both, because "how many assay records" and "how many distinct techniques"
# answer different questions — one technique routinely measures different endpoints in
# different Key Events, which is exactly what the reuse examples below show.
tech_docs = [d for d in solr_docs if d["type_s"] == "assay_technique"]
ke_assay_by_tech: dict[str, list[dict]] = defaultdict(list)
for d in solr_docs:
    if d["type_s"] == "ke_assay":
        ke_assay_by_tech[d["technique_id_s"]].append(d)

# Most-reused techniques, each with the distinct endpoints it is used to measure — the
# concrete illustration of why technique count < record count.
reuse_examples = []
for d in sorted(tech_docs, key=lambda t: -len(t["ke_ids_ss"]))[:15]:
    linked = ke_assay_by_tech.get(d["id"], [])
    endpoints = sorted({(r.get("measured_endpoint_t") or "").strip()
                        for r in linked if (r.get("measured_endpoint_t") or "").strip()})
    reuse_examples.append({
        "technique_id": d["id"],
        "name": d["name_t"],
        "method_type": d["method_type_s"],
        "n_ke": len(d["ke_ids_ss"]),
        "n_records": len(linked),
        "n_distinct_endpoints": len(endpoints),
        "endpoints_sample": endpoints[:12],
        "n_synonyms": len(d.get("attr_synonyms") or []),
    })

ke_per_tech = sorted((len(d["ke_ids_ss"]) for d in tech_docs), reverse=True)
stats = {
    "assay_min_support": int(assay_min_support),
    "iri_min_freq": int(iri_min_freq),
    "n_records_indexed": n_ke_assay,
    "n_ke_assay_id_collisions": n_ke_assay_id_collisions,
    # absorbed technique id -> surviving id, for consumers / Solr cleanup
    "retired_technique_ids": retired_technique_ids,
    "n_techniques": n_tech,
    "n_ke_covered": len(assays_ke_cleaned),
    "records_per_technique_mean": (n_ke_assay / n_tech) if n_tech else None,
    # a technique used in exactly one KE is not evidence of reuse; the rest are
    "n_techniques_single_ke": sum(1 for n in ke_per_tech if n == 1),
    "n_techniques_multi_ke": sum(1 for n in ke_per_tech if n > 1),
    "max_ke_per_technique": ke_per_tech[0] if ke_per_tech else 0,
    "reuse_examples": reuse_examples,
}
out_stats = Path(product["stats"])
out_stats.parent.mkdir(parents=True, exist_ok=True)
with open(out_stats, "w", encoding="utf-8") as f:
    json.dump(stats, f, ensure_ascii=False, indent=2)
logger.info(
    f"wrote {out_stats}: {n_tech} techniques from {n_ke_assay} records "
    f"({stats['n_techniques_multi_ke']} techniques span >1 KE, "
    f"max {stats['max_ke_per_technique']})"
)
