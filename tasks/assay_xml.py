"""
assay_xml.py — curated Methods2AOP assays -> XML. LIBRARY, not a task.

Maps the flat per-(KE, assay) records and the technique dictionary that
tasks/assay_consensus2solr.py emits as its `xml_records` product onto the xsdata-pydantic
classes generated from resources/methods2aop_schema_proposal.xsd (aopkb/methods2aop.py,
see tasks/gen_assay_bindings.py), writes the document and validates it. One schema, one
validation pass.

The document follows the AOP-Wiki export conventions: every object carries only a GUID
`id`; the human-readable stable ids live in a <lookup> section of the <assays> container
(<key-event-reference id aop-wiki-id>, <technique-reference id technique-id>,
<assay-reference id assay-id>).

    <assays>
      <techniques><technique id>name, synonym*</technique>*</techniques>
      <assay id ke-id technique-id support models><method>...</method></assay>*
      <lookup>...</lookup>
    </assays>

The mapping of a record onto <method> is hand-written because AssayRecord
(tasks/assay_models.py) is not generated from the XSD:
    organ_tissue            -> organ-tissues/organ-tissue
    species                 -> species/taxon
    evidence                -> evidence/source-text
    ontology_terms{name,id} -> ontology-terms/ontology-term{label,iri,ontology}
`kec_triple` and other Solr-only fields have no element and are not exported.

Everything lossy is counted in an XmlReport (coerced enums, dropped ontology terms,
unknown prefixes, skipped records, ...) rather than silently swallowed.

Import-safe (no module-level side effects), unlike the Ploomber task scripts — same
pattern as tasks/assay_models.py.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lxml import etree
from xsdata.formats.dataclass.serializers.config import SerializerConfig
from xsdata_pydantic.bindings import XmlSerializer

from aopkb.methods2aop import (
    Assays,
    EndpointDirectionEnum,
    MeasurementMethodType,
    MethodTypeEnum,
    OntologyTermType,
    ThroughputEnum,
)

M2A_NS = "http://www.aopkb.org/aop-xml"

# Fixed namespace for the uuid5 GUIDs of assays and techniques. A constant URL, so it can
# be re-derived; changing it changes every GUID.
ASSAY_UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL,
                                  "https://aopkb.org/aopmapper/assays")

# Short-form ontology id ("GO_0006954", as written by tasks.kec_compare.iri_to_solr_id)
# -> canonical IRI. Most prefixes are OBO Foundry purls; the exceptions are listed.
# Anything not listed is omitted from the XML and counted (we do not guess a base).
_OBO = "http://purl.obolibrary.org/obo/"
OBO_PREFIXES = frozenset({
    "GO", "UBERON", "CL", "CHEBI", "PR", "CHMO", "OBI", "PATO", "MMO", "FIX", "UO",
    "HP", "DOID", "MP", "NCIT", "ENVO", "CLO", "MI", "RO", "BTO", "CHEMINF", "OBA",
    "SO", "MONDO", "NBO", "VT", "MS", "FAO",
})
SPECIAL_BASES = {
    # NOTE: CHMO and NCIT are deliberately in OBO_PREFIXES, i.e. the OBO purl form
    # (http://purl.obolibrary.org/obo/CHMO_...), which is what OLS and the proposal
    # XSD example use; they have no separate namespace of their own.
    # BAO is NOT an OBO purl: it uses its own namespace with a '#'
    "BAO": "http://www.bioassayontology.org/bao#BAO_",
    "EFO": "http://www.ebi.ac.uk/efo/EFO_",
    # iri_to_solr_id upper-cases the prefix; the OBO purl spells it NCBITaxon
    "NCBITAXON": _OBO + "NCBITaxon_",
    "FMA": "http://purl.org/sig/ont/fma/fma",
}
_SHORT_ID = re.compile(r"^([A-Za-z][A-Za-z0-9]*)_([A-Za-z0-9][A-Za-z0-9._-]*)$")
_KE_ID = re.compile(r"^KE(\d+)$")

# characters that are illegal in XML 1.0 (LLM output occasionally contains them)
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")

_METHOD_TYPES = {e.value for e in MethodTypeEnum}
_DIRECTIONS = {e.value for e in EndpointDirectionEnum}
_THROUGHPUTS = {e.value for e in ThroughputEnum}

# obvious inflections of enum values, mapped before falling back to not_specified
_INFLECTIONS = {
    "increased": "increase", "increases": "increase", "increasing": "increase",
    "decreased": "decrease", "decreases": "decrease", "decreasing": "decrease",
    "changed": "change", "changes": "change", "changing": "change",
}


@dataclass
class XmlReport:
    """Counts of everything the export had to coerce, drop or skip."""
    n_records: int = 0
    n_assays: int = 0
    n_kes: int = 0
    n_techniques: int = 0
    n_ontology_terms: int = 0
    coerced_enums: Counter = field(default_factory=Counter)        # field -> n
    coerced_values: Counter = field(default_factory=Counter)       # "field=value" -> n
    mapped_inflections: Counter = field(default_factory=Counter)   # "field=value" -> n
    dropped_terms: int = 0
    unknown_prefixes: Counter = field(default_factory=Counter)     # prefix -> n
    # records not exported at all (their terms/enums are not counted above)
    records_without_name: int = 0
    records_without_ke_number: int = 0     # ke_id is not "KE<n>"
    records_without_ke_uuid: int = 0       # KE has no AOP-Wiki UUID
    # exported, but without the link / lookup entry
    records_without_technique: int = 0     # technique_id missing or not in techniques
    records_without_assay_id: int = 0      # no stable id -> no assay-reference
    guid_collisions: int = 0               # same (KE, name, endpoint): ordinal suffix
    techniques_not_exported: int = 0       # referenced by no exported assay
    empty_guideline_omitted: int = 0
    stripped_control_chars: int = 0
    validation_failures: int = 0
    validation_errors: list = field(default_factory=list)          # first few messages

    def to_dict(self) -> dict:
        return {
            "n_records": self.n_records,
            "n_assays": self.n_assays,
            "n_kes": self.n_kes,
            "n_techniques": self.n_techniques,
            "n_ontology_terms": self.n_ontology_terms,
            "coerced_enums": dict(self.coerced_enums),
            "coerced_values": dict(self.coerced_values.most_common(50)),
            "mapped_inflections": dict(self.mapped_inflections),
            "dropped_terms": self.dropped_terms,
            "unknown_prefixes": dict(self.unknown_prefixes),
            "records_without_name": self.records_without_name,
            "records_without_ke_number": self.records_without_ke_number,
            "records_without_ke_uuid": self.records_without_ke_uuid,
            "records_without_technique": self.records_without_technique,
            "records_without_assay_id": self.records_without_assay_id,
            "guid_collisions": self.guid_collisions,
            "techniques_not_exported": self.techniques_not_exported,
            "empty_guideline_omitted": self.empty_guideline_omitted,
            "stripped_control_chars": self.stripped_control_chars,
            "validation_failures": self.validation_failures,
            "validation_errors": self.validation_errors[:20],
        }


# ── small helpers ─────────────────────────────────────────────────────────────

def _clean(text, report: XmlReport) -> str:
    """str -> stripped, XML-legal text ("" for None)."""
    if text is None:
        return ""
    cleaned, n = _XML_ILLEGAL.subn("", str(text))
    report.stripped_control_chars += n
    return cleaned.strip()


def _has_text(text) -> bool:
    return bool(_XML_ILLEGAL.sub("", str(text or "")).strip())


def _clean_list(values, report: XmlReport) -> list[str]:
    """Stripped, empty-free, order-preserving de-duplicated list."""
    out: list[str] = []
    for v in values or []:
        s = _clean(v, report)
        if s and s not in out:
            out.append(s)
    return out


def _norm(text) -> str:
    """Whitespace-collapsed, case-folded text for ids and sort keys."""
    return re.sub(r"\s+", " ", str(text or "").strip()).casefold()


def _norm_enum(value, allowed: set[str], fld: str, report: XmlReport) -> str:
    """Case/space/hyphen tolerant match onto a closed enum, with obvious inflections
    ("increased") mapped to their base value; anything else becomes not_specified and is
    counted. None/"" is the default, not a coercion."""
    if value is None or str(value).strip() == "":
        return "not_specified"
    norm = re.sub(r"[\s-]+", "_", str(value).strip().lower())
    if norm in allowed:
        return norm
    base = _INFLECTIONS.get(norm)
    if base in allowed:
        report.mapped_inflections[f"{fld}={norm}"] += 1
        return base
    report.coerced_enums[fld] += 1
    report.coerced_values[f"{fld}={str(value).strip()}"] += 1
    return "not_specified"


def expand_iri(short_id: Optional[str]) -> Optional[tuple[str, str]]:
    """Short-form id ("GO_0006954", "BAO_0000179") -> (IRI, prefix), or None when the id
    is malformed or its prefix is not in the explicit map."""
    if not short_id:
        return None
    m = _SHORT_ID.match(str(short_id).strip())
    if not m:
        return None
    prefix, local = m.group(1).upper(), m.group(2)
    if prefix in SPECIAL_BASES:
        return SPECIAL_BASES[prefix] + local, prefix
    if prefix in OBO_PREFIXES:
        return f"{_OBO}{prefix}_{local}", prefix
    return None


def ke_number(ke_id) -> Optional[int]:
    """"KE123" -> 123 (the AOP-Wiki number), None for anything else."""
    m = _KE_ID.match(str(ke_id or "").strip())
    return int(m.group(1)) if m else None


def assay_guid(ke_uuid: str, assay_name: str, endpoint: str = "",
               ordinal: int = 1) -> str:
    """Deterministic uuid5 over (KE UUID, normalised assay name, normalised measured
    endpoint). Deterministic per input, not promised stable across releases (the stable
    handle is the assay-id in the lookup). `ordinal` > 1 only separates exact repeats of
    the same triple."""
    key = f"assay|{str(ke_uuid).strip().lower()}|{_norm(assay_name)}|{_norm(endpoint)}"
    if ordinal > 1:
        key += f"|{ordinal}"
    return str(uuid.uuid5(ASSAY_UUID_NAMESPACE, key))


def technique_guid(technique_id: str) -> str:
    """Deterministic uuid5 of a technique, keyed on the pipeline's technique id
    (TECH<n>, a permanent sequential number)."""
    return str(uuid.uuid5(ASSAY_UUID_NAMESPACE, f"technique|{technique_id}"))


# ── records -> classes ────────────────────────────────────────────────────────

def _ontology_terms(rec: dict, report: XmlReport) -> list[OntologyTermType]:
    terms = rec.get("ontology_terms")
    if terms is None:                               # older record shape: ids only
        terms = [{"name": "", "id": i} for i in rec.get("ontology_ids") or []]
    out: list[OntologyTermType] = []
    for t in terms:
        expanded = expand_iri(t.get("id"))
        if expanded is None:
            report.dropped_terms += 1
            prefix = str(t.get("id") or "").split("_")[0].upper() or "(none)"
            report.unknown_prefixes[prefix] += 1
            continue
        iri, prefix = expanded
        label = _clean(t.get("name"), report) or str(t["id"])
        out.append(OntologyTermType(label=label, iri=iri, ontology=prefix))
    report.n_ontology_terms += len(out)
    return out


def record_to_method(rec: dict, report: XmlReport) -> Optional[MeasurementMethodType]:
    """One flat record -> MeasurementMethodType; None if the record has no assay name
    (nothing is counted in that case). Empty wrappers (cell-types, ontology-terms, ...)
    and an empty guideline are omitted, as the XSD sequences require at least one child."""
    if not _has_text(rec.get("assay_name")):
        return None
    name = _clean(rec.get("assay_name"), report)

    def wrap(cls, attr, values):
        return cls(**{attr: values}) if values else None

    guideline = _clean(rec.get("guideline"), report)
    if not guideline and str(rec.get("guideline") or "") != "":
        report.empty_guideline_omitted += 1
    terms = _ontology_terms(rec, report)
    M = MeasurementMethodType
    return M(
        assay_name=name,
        description=_clean(rec.get("description"), report) or None,
        method_type=MethodTypeEnum(
            _norm_enum(rec.get("method_type"), _METHOD_TYPES, "method_type", report)),
        cell_types=wrap(M.CellTypes, "cell_type",
                        _clean_list(rec.get("cell_types"), report)),
        organ_tissues=wrap(M.OrganTissues, "organ_tissue",
                           _clean_list(rec.get("organ_tissue"), report)),
        species=wrap(M.Species, "taxon", _clean_list(rec.get("species"), report)),
        measured_endpoint=_clean(rec.get("measured_endpoint"), report) or None,
        endpoint_direction=EndpointDirectionEnum(_norm_enum(
            rec.get("endpoint_direction"), _DIRECTIONS, "endpoint_direction", report)),
        ontology_terms=wrap(M.OntologyTerms, "ontology_term", terms),
        guideline=guideline or None,
        throughput=ThroughputEnum(
            _norm_enum(rec.get("throughput"), _THROUGHPUTS, "throughput", report)),
        evidence=wrap(M.Evidence, "source_text", _clean_list(rec.get("evidence"), report)),
    )


def _sort_key(rec: dict):
    ke = str(rec.get("ke_id") or "")
    m = re.search(r"\d+", ke)
    # endpoint/description/support break ties between same-named assays of one KE, so
    # the output order (and the ordinals of exact repeats) do not depend on input order
    return (int(m.group()) if m else 10**9, ke, _norm(rec.get("assay_name")),
            _norm(rec.get("measured_endpoint")), _norm(rec.get("description")),
            -int(rec.get("support") or 0),
            json.dumps(rec.get("models") or [], sort_keys=True),
            str(rec.get("id") or ""))


def build_assays(records: list[dict], report: Optional[XmlReport] = None,
                 techniques: Optional[list[dict]] = None) -> Assays:
    """Records (+ technique dictionary) -> the `Assays` root, ordered by (KE number,
    assay name, ...) so the output is deterministic whatever the input order.

    A record is exported only with an assay name, a "KE<n>" id and the KE's AOP-Wiki
    UUID (`ke_uuid`); the others are counted in the report. Techniques referenced by no
    exported assay are not exported."""
    report = report if report is not None else XmlReport()
    report.n_records = len(records)

    tech_info: dict[str, dict] = {}                 # TECH id -> cleaned technique
    for t in techniques or []:
        name = _clean(t.get("name"), report)
        if t.get("id") and name:
            tech_info[str(t["id"])] = {
                "name": name, "synonyms": _clean_list(t.get("synonyms"), report)}

    seen: Counter = Counter()
    ke_uuids: dict[str, int] = {}                   # exported KE UUID -> AOP-Wiki number
    used_tech: set[str] = set()
    assays: list[Assays.Assay] = []
    assay_refs: list[Assays.Lookup.AssayReference] = []

    for rec in sorted(records, key=_sort_key):
        if not _has_text(rec.get("assay_name")):
            report.records_without_name += 1
            continue
        number = ke_number(rec.get("ke_id"))
        if number is None:
            report.records_without_ke_number += 1
            continue
        ke_uuid = str(rec.get("ke_uuid") or "").strip()
        if not ke_uuid:
            report.records_without_ke_uuid += 1
            continue

        method = record_to_method(rec, report)
        key = (ke_uuid.lower(), _norm(method.assay_name),
               _norm(method.measured_endpoint))
        seen[key] += 1
        if seen[key] > 1:
            report.guid_collisions += 1
        gid = assay_guid(ke_uuid, method.assay_name, method.measured_endpoint or "",
                         seen[key])

        tid = str(rec.get("technique_id") or "")
        if tid in tech_info:
            used_tech.add(tid)
            tech_guid = technique_guid(tid)
        else:
            tech_guid = None
            report.records_without_technique += 1

        models = sorted({re.sub(r"\s+", "_", _clean(m, report))
                         for m in rec.get("models") or [] if _clean(m, report)})
        assays.append(Assays.Assay(
            method=method, id=gid, ke_id=ke_uuid, technique_id=tech_guid,
            support=int(rec["support"]) if rec.get("support") is not None else None,
            models=models,
        ))
        ke_uuids[ke_uuid] = number

        stable = _clean(rec.get("id"), report)
        if stable:
            assay_refs.append(Assays.Lookup.AssayReference(id=gid, assay_id=stable))
        else:
            report.records_without_assay_id += 1

    report.n_assays = len(assays)
    report.n_kes = len(ke_uuids)
    report.n_techniques = len(used_tech)
    report.techniques_not_exported = len(tech_info) - len(used_tech)

    tech_objs = [
        Assays.Techniques.Technique(id=technique_guid(tid), name=info["name"],
                                    synonym=info["synonyms"])
        for tid, info in sorted(tech_info.items(),
                                key=lambda kv: (_norm(kv[1]["name"]), kv[0]))
        if tid in used_tech]
    lookup = Assays.Lookup(
        key_event_reference=[
            Assays.Lookup.KeyEventReference(id=u, aop_wiki_id=n)
            for u, n in sorted(ke_uuids.items(), key=lambda kv: (kv[1], kv[0]))],
        technique_reference=[
            Assays.Lookup.TechniqueReference(id=technique_guid(tid), technique_id=tid)
            for tid in sorted(used_tech)],
        assay_reference=assay_refs,
    ) if assays else None
    return Assays(
        techniques=Assays.Techniques(technique=tech_objs) if tech_objs else None,
        assay=assays, lookup=lookup)


# ── serialisation, validation, HTML ───────────────────────────────────────────

def to_xml(assays: Assays) -> str:
    """Pretty-printed XML text, everything in the (default) aop-xml namespace."""
    cfg = SerializerConfig(indent="  ", xml_declaration=True, encoding="UTF-8")
    return XmlSerializer(config=cfg).render(assays, ns_map={None: M2A_NS})


def write_xml(assays: Assays, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_xml(assays), encoding="utf-8")
    return path


def validate(xml_path, xsd_path, report: Optional[XmlReport] = None) -> list[str]:
    """Validate an assays document against methods2aop_schema_proposal.xsd with
    xmlschema's XSD 1.1 validator (single pass). Returns the error messages; when a
    report is given, also records the failure count and the first messages."""
    import xmlschema                                     # heavy; only needed here

    schema = xmlschema.XMLSchema11(str(xsd_path))
    errors = [f"{e.path}: {e.reason}" for e in schema.iter_errors(str(xml_path))]
    if report is not None:
        report.validation_failures = len(errors)
        report.validation_errors = errors[:20]
    return errors


def render_html(xml_path, xslt_path, html_path) -> Path:
    """Apply the XSLT with lxml and write static HTML (browsers block XSLT processing
    instructions over file://, so the HTML is produced here instead)."""
    transform = etree.XSLT(etree.parse(str(xslt_path)))
    result = transform(etree.parse(str(xml_path)))
    html_path = Path(html_path)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_bytes(etree.tostring(result, method="html", encoding="UTF-8",
                                         doctype="<!DOCTYPE html>"))
    return html_path
