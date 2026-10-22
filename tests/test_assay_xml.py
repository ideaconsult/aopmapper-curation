"""Tests for the assay XML export (tasks/assay_xml.py): mapping of the flat
assay_consensus2solr `xml_records` onto the classes generated from the single
resources/methods2aop_schema_proposal.xsd, serialisation, XSD 1.1 validation (xmlschema,
single pass) and the XSLT view. Inline fixtures, no pipeline products."""
import re
from pathlib import Path

import pytest
from lxml import etree
from xsdata_pydantic.bindings import XmlParser

from aopkb.methods2aop import Assays
from tasks import assay_xml as ax

RESOURCES = Path(__file__).resolve().parent.parent / "resources"
XSD = RESOURCES / "methods2aop_schema_proposal.xsd"
ASSAYS_XSL = RESOURCES / "xslt" / "assays.xsl"

NS = {"m": ax.M2A_NS}

KE_UUID = {
    "KE10": "586f2d96-c0c8-46c8-a1e7-3f2e66a437ac",
    "KE2": "b2a0b9bf-7f90-48e6-8fff-80759614bce9",
    "KE7": "3decbcda-acbe-4591-8543-e16edd620344",
}
ASSAY_N = {"KE10": 101, "KE2": 102, "KE7": 103}   # permanent ASSAY<n> numbers
TECHNIQUES = [
    {"id": "TECH1", "name": "DAPI", "synonyms": ["DAPI staining"]},
    {"id": "TECH2", "name": "MTT", "synonyms": []},
    {"id": "TECH3", "name": "unused technique", "synonyms": []},
]


def rec(**kw):
    ke = kw.get("ke_id", "KE10")
    base = {
        "ke_id": ke, "ke_uuid": KE_UUID.get(ke),
        "id": f"ASSAY{ASSAY_N.get(ke, 999)}", "technique_id": "TECH1",
        "assay_name": "DAPI", "description": "nuclear stain",
        "method_type": "in_vitro", "measured_endpoint": "cell count",
        "endpoint_direction": "decrease",
        "ontology_ids": ["BAO_0000179", "GO_0008283"],
        "ontology_terms": [
            {"name": "DAPI staining", "id": "BAO_0000179", "source": "BAO"},
            {"name": "cell count", "id": "GO_0008283", "source": "GO"}],
        "cell_types": ["THP-1", "A549"], "organ_tissue": ["lung"], "species": ["human"],
        "guideline": "OECD TG 487", "throughput": "high",
        "evidence": ["DAPI staining is used to count nuclei"],
        "support": 7, "models": ["google--gemma-3-27b-it", "Qwen--Qwen3.6-27B"],
        "kec_triple": None,                     # exported only when given (see below)
    }
    base.update(kw)
    return base


def build(records, techniques=None):
    report = ax.XmlReport()
    techniques = TECHNIQUES if techniques is None else techniques
    return ax.build_assays(records, report, techniques), report


def write(tmp_path, records, name="assays.xml", techniques=None):
    assays, report = build(records, techniques)
    return ax.write_xml(assays, tmp_path / name), report


def root_of(path):
    return etree.parse(str(path)).getroot()


def two_ke_records():
    return [rec(), rec(ke_id="KE2", assay_name="MTT", technique_id="TECH2",
                       id="ASSAY102")]


# ── round trip ────────────────────────────────────────────────────────────────

def test_round_trip_classes_to_xml_and_back(tmp_path):
    assays, _ = build(two_ke_records())
    path = ax.write_xml(assays, tmp_path / "a.xml")

    parsed = XmlParser().from_path(path, Assays)
    assert parsed.model_dump() == assays.model_dump()
    # sorted by KE number, so KE2 precedes KE10
    assert [a.ke_id for a in parsed.assay] == [KE_UUID["KE2"], KE_UUID["KE10"]]
    dapi = parsed.assay[1]
    assert dapi.support == 7
    assert dapi.models == ["Qwen--Qwen3.6-27B", "google--gemma-3-27b-it"]
    m = dapi.method
    assert m.assay_name == "DAPI" and m.method_type.value == "in_vitro"
    assert m.cell_types.cell_type == ["THP-1", "A549"]
    assert m.organ_tissues.organ_tissue == ["lung"]
    assert m.species.taxon == ["human"]
    assert m.evidence.source_text == ["DAPI staining is used to count nuclei"]
    assert [t.label for t in m.ontology_terms.ontology_term] == ["DAPI staining",
                                                                 "cell count"]


def test_single_namespace_and_unexported_fields(tmp_path):
    path, _ = write(tmp_path, [rec(kec_triple=None)])
    text = path.read_text(encoding="utf-8")
    root = root_of(path)
    assert root.tag == f"{{{ax.M2A_NS}}}assays"
    assert root.find("m:assay/m:method/m:assay-name", NS).text == "DAPI"
    assert "kec_triple" not in text and "biological" not in text


# ── validation ────────────────────────────────────────────────────────────────

def test_output_validates_against_the_single_xsd(tmp_path):
    path, report = write(tmp_path, two_ke_records())
    assert ax.validate(path, XSD, report) == []
    assert report.validation_failures == 0


def test_validation_rejects_a_bad_enum(tmp_path):
    path, report = write(tmp_path, [rec()])
    path.write_text(path.read_text(encoding="utf-8").replace(
        "<method-type>in_vitro<", "<method-type>in vitro<"), encoding="utf-8")
    errors = ax.validate(path, XSD, report)
    assert errors and report.validation_failures == len(errors)


@pytest.mark.parametrize("attr", ["id", "ke-id", "technique-id"])
def test_validation_rejects_a_non_guid(tmp_path, attr):
    path, _ = write(tmp_path, [rec()])
    path.write_text(re.sub(rf'(<assay [^>]*?\b{attr}=)"[^"]+"', r'\1"ASSAY1"',
                           path.read_text(encoding="utf-8")), encoding="utf-8")
    assert ax.validate(path, XSD)


def test_validation_rejects_duplicate_assay_guid_and_dangling_technique(tmp_path):
    path, _ = write(tmp_path, two_ke_records())
    text = path.read_text(encoding="utf-8")
    ids = re.findall(r'<assay id="([^"]+)"', text)
    dup = path.parent / "dup.xml"
    dup.write_text(text.replace(ids[1], ids[0]), encoding="utf-8")
    assert ax.validate(dup, XSD)                          # xs:unique on assay/@id
    tech = re.search(r'technique-id="([^"]+)"', text).group(1)
    dangling = path.parent / "dangling.xml"
    dangling.write_text(text.replace(
        f'technique-id="{tech}"', 'technique-id="00000000-0000-5000-8000-000000000000"'),
        encoding="utf-8")
    assert ax.validate(dangling, XSD)                     # xs:keyref assay-technique


def test_minimal_record_is_valid(tmp_path):
    path, _ = write(tmp_path, [{"ke_id": "KE2", "ke_uuid": KE_UUID["KE2"],
                                "assay_name": "ELISA"}], techniques=[])
    assert ax.validate(path, XSD) == []


def test_empty_document_is_valid(tmp_path):
    path, report = write(tmp_path, [])
    assert report.n_assays == 0
    assert ax.validate(path, XSD) == []


def test_proposal_xsd_declarations_and_comments():
    """The file is well-formed (no `--` runs / nested comments); the original types and
    enums are unchanged, and the container declarations are the ones the code relies on."""
    xs = "{http://www.w3.org/2001/XMLSchema}"
    root = etree.parse(str(XSD)).getroot()
    declared = {(el.tag.replace(xs, ""), el.get("name"))
                for el in root.iter() if el.get("name")}
    original = {
        ("simpleType", "method-type-enum"), ("simpleType", "endpoint-direction-enum"),
        ("simpleType", "throughput-enum"),
        ("complexType", "ontology-term-type"), ("complexType", "measurement-method-type"),
        ("element", "label"), ("element", "iri"), ("element", "ontology"),
        ("element", "assay-name"), ("element", "description"),
        ("element", "method-type"), ("element", "cell-types"), ("element", "cell-type"),
        ("element", "organ-tissues"), ("element", "organ-tissue"),
        ("element", "species"), ("element", "taxon"),
        ("element", "measured-endpoint"), ("element", "endpoint-direction"),
        ("element", "ontology-terms"), ("element", "ontology-term"),
        ("element", "guideline"), ("element", "throughput"),
        ("element", "evidence"), ("element", "source-text"),
        ("element", "key-event-extension-example"),
        ("element", "measurement-methodology"), ("element", "measurement-methods"),
        ("element", "method"),
    }
    assert original <= declared
    top = {el.get("name") for el in root if el.tag == f"{xs}element"}
    assert top == {"assays", "key-event-extension-example"}
    enums = {
        el.get("name"): [v.get("value") for v in el.iter(f"{xs}enumeration")]
        for el in root.iter(f"{xs}simpleType") if el.get("name", "").endswith("-enum")}
    assert enums == {
        "method-type-enum": ["in_vitro", "in_vivo", "in_silico", "ex_vivo",
                             "not_specified"],
        "endpoint-direction-enum": ["increase", "decrease", "change", "not_specified"],
        "throughput-enum": ["high", "medium", "low", "not_specified"],
    }
    # no XML-illegal comment content
    for c in root.xpath("//comment()") + etree.parse(str(XSD)).xpath("/comment()"):
        assert "--" not in c.text


# ── omission of empty content ─────────────────────────────────────────────────

def test_empty_wrappers_and_guideline_are_omitted(tmp_path):
    path, report = write(tmp_path, [rec(
        cell_types=[], organ_tissue=["", "  "], species=None, evidence=[],
        ontology_terms=[], ontology_ids=[], guideline="", description="  ",
        measured_endpoint="")])
    method = root_of(path).find("m:assay/m:method", NS)
    present = {etree.QName(c).localname for c in method}
    assert present == {"assay-name", "method-type", "endpoint-direction", "throughput"}
    assert ax.validate(path, XSD) == []


def test_whitespace_only_guideline_is_counted_and_omitted(tmp_path):
    path, report = write(tmp_path, [rec(guideline="   ")])
    assert root_of(path).find("m:assay/m:method/m:guideline", NS) is None
    assert report.empty_guideline_omitted == 1


def test_illegal_control_characters_are_stripped(tmp_path):
    path, report = write(tmp_path, [rec(description="a\x00b\x0bc")])
    assert root_of(path).find("m:assay/m:method/m:description", NS).text == "abc"
    assert report.stripped_control_chars == 2


# ── skipped records and report accounting ─────────────────────────────────────

def test_skipped_records_are_counted_by_cause_and_add_nothing_else():
    bad = dict(ontology_terms=[{"name": "x", "id": "ZZZ_1"}],
               method_type="bogus", guideline="  ")
    assays, report = build([
        rec(assay_name="  ", **bad),                          # no name
        rec(**{**bad, "ke_id": "weird"}),                     # not KE<n>
        rec(**{**bad, "ke_id": "KE7", "ke_uuid": None}),      # KE without UUID
        rec(),
    ])
    assert len(assays.assay) == 1
    assert (report.records_without_name, report.records_without_ke_number,
            report.records_without_ke_uuid) == (1, 1, 1)
    # the three skipped records contributed no terms, coercions or drops
    assert report.n_records == 4 and report.n_assays == 1
    assert report.n_ontology_terms == 2 and report.dropped_terms == 0
    assert not report.coerced_enums and report.empty_guideline_omitted == 0
    assert not report.unknown_prefixes


def test_report_counts():
    _, report = build(two_ke_records())
    d = report.to_dict()
    assert (d["n_records"], d["n_assays"], d["n_kes"], d["n_techniques"]) == (2, 2, 2, 2)
    assert d["n_ontology_terms"] == 4 and d["dropped_terms"] == 0
    assert d["techniques_not_exported"] == 1             # "unused technique"


# ── enum coercion ─────────────────────────────────────────────────────────────

def test_free_string_enums_are_coerced_and_counted(tmp_path):
    path, report = write(tmp_path, [
        rec(method_type="in cell culture", endpoint_direction="both", throughput="ultra"),
        rec(ke_id="KE2", method_type="In-Vitro", endpoint_direction=None, throughput=""),
    ])
    m = root_of(path).findall("m:assay/m:method", NS)
    first, second = m[1], m[0]                  # KE2 sorts first
    assert first.find("m:method-type", NS).text == "not_specified"
    assert first.find("m:endpoint-direction", NS).text == "not_specified"
    assert first.find("m:throughput", NS).text == "not_specified"
    assert second.find("m:method-type", NS).text == "in_vitro"     # tolerant match
    assert second.find("m:endpoint-direction", NS).text == "not_specified"
    assert dict(report.coerced_enums) == {"method_type": 1, "endpoint_direction": 1,
                                          "throughput": 1}
    assert report.coerced_values["endpoint_direction=both"] == 1
    assert ax.validate(path, XSD) == []


@pytest.mark.parametrize("value, expected", [
    ("increased", "increase"), ("Decreased", "decrease"), ("decreasing", "decrease"),
    ("changed", "change"),
])
def test_obvious_inflections_map_to_the_enum_value(tmp_path, value, expected):
    path, report = write(tmp_path, [rec(endpoint_direction=value)])
    got = root_of(path).find("m:assay/m:method/m:endpoint-direction", NS).text
    assert got == expected
    assert not report.coerced_enums
    assert report.mapped_inflections[f"endpoint_direction={value.lower()}"] == 1


def test_inflection_without_an_enum_base_is_still_coerced(tmp_path):
    # "increased" has no base in the throughput enum -> not_specified, counted
    path, report = write(tmp_path, [rec(throughput="increased")])
    assert root_of(path).find("m:assay/m:method/m:throughput", NS).text == "not_specified"
    assert report.coerced_enums["throughput"] == 1


# ── ontology prefix expansion ─────────────────────────────────────────────────

@pytest.mark.parametrize("short, iri, prefix", [
    ("GO_0006954", "http://purl.obolibrary.org/obo/GO_0006954", "GO"),
    ("UBERON_0002048", "http://purl.obolibrary.org/obo/UBERON_0002048", "UBERON"),
    ("CHMO_0002751", "http://purl.obolibrary.org/obo/CHMO_0002751", "CHMO"),
    ("NCIT_C12345", "http://purl.obolibrary.org/obo/NCIT_C12345", "NCIT"),
    # BAO is not an OBO purl
    ("BAO_0000179", "http://www.bioassayontology.org/bao#BAO_0000179", "BAO"),
    ("EFO_0000001", "http://www.ebi.ac.uk/efo/EFO_0000001", "EFO"),
    ("NCBITAXON_9606", "http://purl.obolibrary.org/obo/NCBITaxon_9606", "NCBITAXON"),
    ("SO_0000704", "http://purl.obolibrary.org/obo/SO_0000704", "SO"),
])
def test_expand_iri(short, iri, prefix):
    assert ax.expand_iri(short) == (iri, prefix)


@pytest.mark.parametrize("bad", [None, "", "D012345", "XYZ_123", "GO_", "not an id"])
def test_expand_iri_unknown_or_malformed_is_none(bad):
    assert ax.expand_iri(bad) is None


def test_unknown_prefix_term_is_dropped_and_counted(tmp_path):
    path, report = write(tmp_path, [rec(ontology_terms=[
        {"name": "DAPI staining", "id": "BAO_0000179", "source": "BAO"},
        {"name": "mystery", "id": "ZZZ_0001", "source": "ZZZ"},
        {"name": "bare", "id": "D012345", "source": "MESH"}])])
    terms = root_of(path).findall("m:assay/m:method/m:ontology-terms/m:ontology-term", NS)
    assert len(terms) == 1
    assert terms[0].find("m:iri", NS).text == \
        "http://www.bioassayontology.org/bao#BAO_0000179"
    assert terms[0].find("m:ontology", NS).text == "BAO"
    assert report.dropped_terms == 2
    assert dict(report.unknown_prefixes) == {"ZZZ": 1, "D012345": 1}
    assert report.n_ontology_terms == 1


def test_all_terms_unknown_leaves_no_empty_wrapper(tmp_path):
    path, _ = write(tmp_path, [rec(ontology_terms=[{"name": "x", "id": "ZZZ_1"}])])
    assert root_of(path).find("m:assay/m:method/m:ontology-terms", NS) is None
    assert ax.validate(path, XSD) == []


def test_label_falls_back_to_id_and_ids_only_records_work(tmp_path):
    path, _ = write(tmp_path, [rec(ontology_terms=None, ontology_ids=["GO_0008283"])])
    term = root_of(path).find("m:assay/m:method/m:ontology-terms/m:ontology-term", NS)
    assert term.find("m:label", NS).text == "GO_0008283"
    assert ax.validate(path, XSD) == []


# ── deterministic, unique GUIDs ───────────────────────────────────────────────

GUID_V5 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-"
                     r"[0-9a-f]{12}")


def test_assay_guid_is_deterministic_uuid5_over_ke_uuid_name_and_endpoint():
    ke = KE_UUID["KE10"]
    g = ax.assay_guid(ke, "DAPI", "cell count")
    assert g == ax.assay_guid(ke.upper(), "  dapi ", " Cell  Count")   # normalised
    assert GUID_V5.fullmatch(g)
    assert g != ax.assay_guid(KE_UUID["KE2"], "DAPI", "cell count")
    assert g != ax.assay_guid(ke, "MTT", "cell count")
    assert g != ax.assay_guid(ke, "DAPI", "apoptosis")                  # endpoint counts
    assert g != ax.assay_guid(ke, "DAPI", "cell count", ordinal=2)
    # pinned: a change here changes every GUID
    assert g == "209cb419-41da-5ddd-ad0e-5162c9662e34"


def test_technique_guid_is_deterministic_and_separate_from_assay_guids():
    t = ax.technique_guid("TECH1")
    assert t == ax.technique_guid("TECH1") and GUID_V5.fullmatch(t)
    assert t != ax.technique_guid("TECH2")


def test_output_is_independent_of_input_order_and_repeatable(tmp_path):
    records = two_ke_records() + [rec(ke_id="KE2", assay_name="ELISA")]
    a, _ = write(tmp_path, records, "a.xml")
    b, _ = write(tmp_path, list(reversed(records)), "b.xml")
    c, _ = write(tmp_path, records, "c.xml")
    assert a.read_bytes() == b.read_bytes() == c.read_bytes()


def test_distinct_endpoints_under_the_same_name_keep_distinct_ordered_ids(tmp_path):
    records = [rec(measured_endpoint="cell count", id="ASSAY201"),
               rec(measured_endpoint="apoptosis", support=3, id="ASSAY202"),
               rec(measured_endpoint="necrosis", support=2, id="ASSAY203")]
    a, ra = write(tmp_path, records, "a.xml")
    b, _ = write(tmp_path, records[::-1], "b.xml")
    assert a.read_bytes() == b.read_bytes()
    ids = re.findall(r'<assay id="([^"]+)"', a.read_text(encoding="utf-8"))
    assert len(set(ids)) == 3 and ra.guid_collisions == 0
    # each id is exactly the (KE, name, endpoint) uuid5, no ordinal involved
    assert set(ids) == {ax.assay_guid(KE_UUID["KE10"], "DAPI", e)
                        for e in ("cell count", "apoptosis", "necrosis")}
    assert ax.validate(a, XSD) == []


def test_exact_repeats_get_distinct_ids_and_are_counted(tmp_path):
    path, report = write(tmp_path, [rec(), rec(description="again", id="ASSAY9")])
    ids = re.findall(r'<assay id="([^"]+)"', path.read_text(encoding="utf-8"))
    assert len(set(ids)) == 2 and report.guid_collisions == 1
    assert ax.validate(path, XSD) == []


def test_all_assay_guids_unique_in_a_larger_document(tmp_path):
    combos = [(ke, n, e) for ke in KE_UUID for n in ("DAPI", "MTT") for e in ("a", "b", "")]
    records = [rec(ke_id=ke, assay_name=n, measured_endpoint=e, id=f"ASSAY{1000 + i}")
               for i, (ke, n, e) in enumerate(combos)]
    path, report = write(tmp_path, records)
    ids = re.findall(r'<assay id="([^"]+)"', path.read_text(encoding="utf-8"))
    assert len(ids) == len(set(ids)) == 18 and report.guid_collisions == 0
    assert ax.validate(path, XSD) == []


# ── KE link, techniques and lookup ────────────────────────────────────────────

def test_ke_link_is_the_wiki_uuid_and_lookup_gives_the_number(tmp_path):
    path, _ = write(tmp_path, two_ke_records())
    root = root_of(path)
    assert {a.get("ke-id") for a in root.findall("m:assay", NS)} == {
        KE_UUID["KE10"], KE_UUID["KE2"]}
    refs = {r.get("id"): r.get("aop-wiki-id")
            for r in root.findall("m:lookup/m:key-event-reference", NS)}
    assert refs == {KE_UUID["KE10"]: "10", KE_UUID["KE2"]: "2"}
    # sorted by number
    assert list(refs.values()) == ["2", "10"]
    assert "KE10" not in path.read_text(encoding="utf-8")      # only the number, in the lookup


def test_lookup_maps_assay_and_technique_guids_to_stable_ids(tmp_path):
    path, _ = write(tmp_path, two_ke_records())
    root = root_of(path)
    assay_refs = {r.get("id"): r.get("assay-id")
                  for r in root.findall("m:lookup/m:assay-reference", NS)}
    guid_by_stable = {v: k for k, v in assay_refs.items()}
    assert set(guid_by_stable) == {"ASSAY101", "ASSAY102"}
    assert set(assay_refs) == {a.get("id") for a in root.findall("m:assay", NS)}
    tech_refs = {r.get("technique-id"): r.get("id")
                 for r in root.findall("m:lookup/m:technique-reference", NS)}
    assert tech_refs == {"TECH1": ax.technique_guid("TECH1"),
                         "TECH2": ax.technique_guid("TECH2")}
    # the stable ids appear ONLY in the lookup, never on the objects themselves
    for a in root.findall("m:assay", NS):
        assert set(a.attrib) <= {"id", "ke-id", "technique-id", "support", "models"}
    assert not any("TECH" in (t.get("id") or "")
                   for t in root.findall("m:techniques/m:technique", NS))


def test_every_assay_technique_id_resolves_to_a_technique(tmp_path):
    path, _ = write(tmp_path, two_ke_records() + [rec(ke_id="KE7")])
    root = root_of(path)
    tech_ids = {t.get("id") for t in root.findall("m:techniques/m:technique", NS)}
    assert len(tech_ids) == 2
    for a in root.findall("m:assay", NS):
        assert a.get("technique-id") in tech_ids
    assert ax.validate(path, XSD) == []


def test_technique_with_several_kes_is_one_object(tmp_path):
    path, report = write(tmp_path, [rec(), rec(ke_id="KE2"), rec(ke_id="KE7")])
    root = root_of(path)
    techs = root.findall("m:techniques/m:technique", NS)
    assert len(techs) == 1 and report.n_techniques == 1
    assert techs[0].find("m:name", NS).text == "DAPI"
    assert [s.text for s in techs[0].findall("m:synonym", NS)] == ["DAPI staining"]
    assert {a.get("technique-id") for a in root.findall("m:assay", NS)} == {
        techs[0].get("id")}
    assert len(root.findall("m:assay", NS)) == 3 and report.n_kes == 3


def test_unreferenced_techniques_are_not_exported_and_counted(tmp_path):
    path, report = write(tmp_path, [rec()])
    names = [t.find("m:name", NS).text
             for t in root_of(path).findall("m:techniques/m:technique", NS)]
    assert names == ["DAPI"]
    assert report.techniques_not_exported == 2            # MTT and "unused technique"
    refs = root_of(path).findall("m:lookup/m:technique-reference", NS)
    assert [r.get("technique-id") for r in refs] == ["TECH1"]


def test_record_with_unknown_technique_is_exported_without_link(tmp_path):
    path, report = write(tmp_path, [rec(technique_id="TECH999"),
                                    rec(ke_id="KE2", technique_id=None)])
    assay = root_of(path).findall("m:assay", NS)
    assert all(a.get("technique-id") is None for a in assay)
    assert report.records_without_technique == 2
    assert root_of(path).find("m:techniques", NS) is None
    assert ax.validate(path, XSD) == []


def test_record_without_stable_assay_id_is_exported_without_lookup_entry(tmp_path):
    path, report = write(tmp_path, [
        rec(id="ASSAY11", measured_endpoint="a"),
        rec(id="ASSAY12", measured_endpoint="b"),
        rec(id=None, measured_endpoint="c")])
    assert report.records_without_assay_id == 1
    assert len(root_of(path).findall("m:assay", NS)) == 3
    assert len(root_of(path).findall("m:lookup/m:assay-reference", NS)) == 2
    assert ax.validate(path, XSD) == []


def test_lookup_is_one_to_one_a_shared_stable_assay_id_fails_validation(tmp_path):
    """assay-reference is 1:1 with assay: two assays sharing a stable id (a collision of
    the pipeline id, reported by assay_consensus2solr) make the document invalid."""
    path, _ = write(tmp_path, [rec(id="ASSAY7", measured_endpoint="a"),
                               rec(id="ASSAY7", measured_endpoint="b")])
    assert ax.validate(path, XSD)


# ── HTML view ─────────────────────────────────────────────────────────────────

def test_html_view_has_ke_and_technique_views_and_links_to_ols(tmp_path):
    path, _ = write(tmp_path, [rec(), rec(assay_name="DAPI blue", measured_endpoint="x"),
                               rec(ke_id="KE2")])
    html = ax.render_html(path, ASSAYS_XSL, tmp_path / "out" / "assays.html")
    text = html.read_text(encoding="utf-8")
    # GUIDs are resolved to readable ids through the lookup
    assert 'id="KE10"' in text and 'id="KE2"' in text
    assert KE_UUID["KE10"] not in text
    assert "https://www.ebi.ac.uk/ols4/search?q=BAO_0000179" in text
    assert "https://www.ebi.ac.uk/ols4/search?q=GO_0008283" in text
    assert "OECD TG 487" in text and "THP-1" in text
    # technique view: one technique, three assays, two Key Events, synonym shown
    assert "TECH1" in text and "DAPI staining" in text
    assert re.search(r"3 assays in\s+2\s+Key Events", text)
    # the assay row shows its technique and the stable assay id
    assert "ASSAY101" in text


# ── optional <kec-triple> on the assay wrapper ────────────────────────────────

TRIPLE = {
    "biological_object": {"name": "cell", "iri": "http://purl.obolibrary.org/obo/CL_0000000",
                          "source": "CL"},
    "biological_process": {"name": "cell population proliferation",
                           "iri": "http://purl.obolibrary.org/obo/GO_0008283",
                           "source": "GO"},
    "biological_action": {"name": "decreased", "iri": None, "source": None},
}


def kec_el(path):
    return root_of(path).find("m:assay/m:kec-triple", NS)


def test_kec_triple_is_a_sibling_of_method_and_frozen_type_is_unchanged():
    xs = "{http://www.w3.org/2001/XMLSchema}"
    root = etree.parse(str(XSD)).getroot()
    mmt = next(el for el in root.iter(f"{xs}complexType")
               if el.get("name") == "measurement-method-type")
    assert "kec-triple" not in {e.get("name") for e in mmt.iter(f"{xs}element")}
    assay = next(el for el in root.iter(f"{xs}element") if el.get("name") == "assay")
    order = [e.get("name") for e in assay.find(f"{xs}complexType/{xs}sequence")
             if e.tag == f"{xs}element"]
    assert order == ["method", "kec-triple"]
    assert assay.find(f"{xs}complexType/{xs}sequence/{xs}element[@name='kec-triple']"
                      ).get("minOccurs") == "0"


def test_kec_triple_round_trip_and_validation(tmp_path):
    assays, report = build([rec(kec_triple=TRIPLE)])
    path = ax.write_xml(assays, tmp_path / "a.xml")
    parsed = XmlParser().from_path(path, Assays)
    assert parsed.model_dump() == assays.model_dump()
    t = parsed.assay[0].kec_triple
    assert t.object_value.label == "cell" and t.object_value.ontology == "CL"
    assert t.process.iri == "http://purl.obolibrary.org/obo/GO_0008283"
    assert t.action.label == "decreased" and t.action.iri is None
    assert t.action.ontology is None
    # in the document the element follows <method>
    assay = root_of(path).find("m:assay", NS)
    assert [etree.QName(c).localname for c in assay] == ["method", "kec-triple"]
    assert [etree.QName(c).localname for c in kec_el(path)] == [
        "object", "process", "action"]
    assert ax.validate(path, XSD, report) == []
    assert report.n_kec_triples == 1 and report.n_triple_terms == 3
    assert report.triple_iris_dropped == 0


def test_kec_triple_is_omitted_when_absent_or_empty(tmp_path):
    empties = [None, {}, {"biological_object": None},
               {"biological_object": {"name": "", "iri": None},
                "biological_action": {"name": "  "}}]
    for i, empty in enumerate(empties):
        path, report = write(tmp_path, [rec(kec_triple=empty)], name=f"e{i}.xml")
        assert kec_el(path) is None
        assert "kec-triple" not in path.read_text(encoding="utf-8")
        assert report.n_kec_triples == 0 and report.n_triple_terms == 0
        assert ax.validate(path, XSD) == []


def test_empty_kec_triple_element_fails_validation(tmp_path):
    path, _ = write(tmp_path, [rec(kec_triple=TRIPLE)])
    text = path.read_text(encoding="utf-8")
    emptied = re.sub(r"<kec-triple>.*?</kec-triple>", "<kec-triple/>", text, flags=re.S)
    assert emptied != text
    bad = tmp_path / "empty.xml"
    bad.write_text(emptied, encoding="utf-8")
    assert ax.validate(bad, XSD)                       # xs:assert count(*) ge 1


def test_kec_triple_slots_use_the_term_prefix_expansion(tmp_path):
    triple = {
        "biological_object": {"name": "acetylcholine",
                              "iri": "http://purl.obolibrary.org/obo/CHEBI_15718"},
        "biological_process": {"name": "bao thing",
                               "iri": "http://www.bioassayontology.org/bao#BAO_0000179"},
        "biological_action": "increased",              # bare string, label only
    }
    path, report = write(tmp_path, [rec(kec_triple=triple)])
    kt = kec_el(path)
    obj = kt.find("m:object", NS)
    assert obj.find("m:iri", NS).text == "http://purl.obolibrary.org/obo/CHEBI_15718"
    assert obj.find("m:ontology", NS).text == "CHEBI"
    proc = kt.find("m:process", NS)
    assert proc.find("m:iri", NS).text == "http://www.bioassayontology.org/bao#BAO_0000179"
    assert proc.find("m:ontology", NS).text == "BAO"
    act = kt.find("m:action", NS)
    assert act.find("m:label", NS).text == "increased"
    assert act.find("m:iri", NS) is None and act.find("m:ontology", NS) is None
    assert ax.validate(path, XSD) == []


def test_kec_triple_unknown_prefix_keeps_the_label_and_is_counted(tmp_path):
    triple = {"biological_object": {"name": "weird thing",
                                    "iri": "http://example.org/onto/ZZZ_0001"},
              "biological_process": {"name": "ok", "iri": "GO_0008283"},
              "biological_action": None}
    path, report = write(tmp_path, [rec(kec_triple=triple)])
    kt = kec_el(path)
    obj = kt.find("m:object", NS)
    assert obj.find("m:label", NS).text == "weird thing"
    assert obj.find("m:iri", NS) is None and obj.find("m:ontology", NS) is None
    assert kt.find("m:process/m:iri", NS).text == (
        "http://purl.obolibrary.org/obo/GO_0008283")          # short ids are accepted
    assert kt.find("m:action", NS) is None
    assert report.triple_iris_dropped == 1
    assert dict(report.triple_unknown_prefixes) == {"ZZZ": 1}
    assert report.dropped_terms == 0 and not report.unknown_prefixes
    d = report.to_dict()
    assert d["n_kec_triples"] == 1 and d["triple_iris_dropped"] == 1
    assert ax.validate(path, XSD) == []


def test_kec_triple_does_not_change_the_other_content(tmp_path):
    with_t, _ = write(tmp_path, [rec(kec_triple=TRIPLE)], name="with.xml")
    without, _ = write(tmp_path, [rec(kec_triple=None)], name="without.xml")
    stripped = re.sub(r"\s*<kec-triple>.*?</kec-triple>", "",
                      with_t.read_text(encoding="utf-8"), flags=re.S)
    assert stripped == without.read_text(encoding="utf-8")


def test_html_view_shows_the_kec_triple_with_ols_links(tmp_path):
    path, _ = write(tmp_path, [rec(kec_triple=TRIPLE)])
    text = ax.render_html(path, ASSAYS_XSL, tmp_path / "o" / "a.html").read_text(
        encoding="utf-8")
    assert "KEC triple" in text
    assert "https://www.ebi.ac.uk/ols4/search?q=CL_0000000" in text
    assert "object:" in text and "process:" in text and "action:" in text
    assert "decreased" in text
    # no triple, no triple lines (the column header remains)
    path2, _ = write(tmp_path, [rec(kec_triple=None)], name="n.xml")
    text2 = ax.render_html(path2, ASSAYS_XSL, tmp_path / "o" / "n.html").read_text(
        encoding="utf-8")
    assert "object:" not in text2 and "process:" not in text2


def test_kec_triple_curie_iri_is_accepted(tmp_path):
    triple = {"biological_process": {"name": "inflammatory response",
                                     "iri": "GO:0006954", "source": "GO"},
              "biological_object": {"name": "x", "iri": "P01863"}}   # bare accession
    path, report = write(tmp_path, [rec(kec_triple=triple)])
    kt = kec_el(path)
    assert kt.find("m:process/m:iri", NS).text == (
        "http://purl.obolibrary.org/obo/GO_0006954")
    assert kt.find("m:object/m:iri", NS) is None            # unmappable: label only
    assert report.triple_iris_dropped == 1
    assert ax.validate(path, XSD) == []
