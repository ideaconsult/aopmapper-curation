"""Tests for the KEC consensus XML export (tasks/kec_xml.py): mapping of viz_consensus data
onto the classes generated from resources/kec_consensus_schema.xsd, term and cluster GUIDs,
serialisation, XSD 1.1 validation (xmlschema), the lxml cross-check and the XSLT view.
Inline fixtures, no pipeline products. The derived statuses are tested in
test_kec_consensus_status.py."""
import copy
import random
import re
from pathlib import Path

import pytest
from lxml import etree
from xsdata_pydantic.bindings import XmlParser

from aopkb.kec_consensus import KecConsensus
from tasks import kec_xml as kx

RESOURCES = Path(__file__).resolve().parent.parent / "resources"
XSD = RESOURCES / "kec_consensus_schema.xsd"
XSL = RESOURCES / "xslt" / "kec_consensus.xsl"

NS = {"c": kx.KEC_NS}
KE_UUID = {
    "KE10": "586f2d96-c0c8-46c8-a1e7-3f2e66a437ac",
    "KE2": "b2a0b9bf-7f90-48e6-8fff-80759614bce9",
}
MODELS = ["m_a", "m_b", "m_c"]
GO = "http://purl.obolibrary.org/obo/GO_0006954"


def slot(name, iri=None, source=None):
    return {"name": name, "iri": iri, "source": source, "lp": -0.1}


INFLAM = slot("inflammatory response", GO, "GO")
INCREASED = slot("increased", None, None)             # models give no id for the action
WIKI_UP = slot("increased", "1", "WIKI")              # the curated action: AOP-Wiki id


def member(model, obj, proc, act, ref=None, method="name", score=0.8, matched=None):
    """A cluster member; `ref` = curated triple it matched, `matched` False = nearest only."""
    if matched is None:
        matched = ref is not None
    return {"model": model, "slots": {"object": obj, "process": proc, "action": act},
            "match": {"matched": matched, "method": method if matched else "none",
                      "score": score, "ref_idx": ref}}


def cl(support, models, rep, members):
    return {"support": support, "models": models,
            "rep": dict(zip(("object", "process", "action"), rep)), "members": members}


def make_viz():
    """KE10: two curated triples, one confirmed by a 3-model cluster, one missed; a novel
    2-model cluster without action; a 1-model cluster below the threshold. KE2: no curated
    triple, one novel cluster. KE99 has no GUID, "foo" no number."""
    ke10 = {
        "ke_id": "KE10", "title": "Inflammation",
        "gt": [
            {"object": slot("cell", "CL_0000000", "CL"), "process": INFLAM, "action": WIKI_UP},
            {"object": None, "process": slot("Increased serum ferritin", "HP:0003281", "HP"),
             "action": slot("decreased", "2", "WIKI")},
        ],
        "clusters": [
            cl(3, MODELS, (None, INFLAM, INCREASED), [
                member("m_a", None, INFLAM, INCREASED, ref=0, method="name", score=0.8),
                member("m_b", None, INFLAM, INCREASED, ref=0, method="iri", score=1.0),
                member("m_c", slot("cell", "CL_0000000", "CL"), INFLAM, INCREASED,
                       ref=1, matched=False),
            ]),
            cl(2, ["m_a", "m_b"], (slot("ferritin", "CHEBI_82594", "CHEBI"),
                                   slot("serum ferritin"), None), [
                member("m_a", slot("ferritin", "CHEBI_82594", "CHEBI"),
                       slot("serum ferritin"), None, matched=False),
                member("m_b", slot("Ferritin", "http://purl.obolibrary.org/obo/CHEBI_82594",
                                   "ChEBI"), slot("serum ferritin"), None, matched=False),
            ]),
            cl(1, ["m_c"], (None, slot("apoptosis", "GO_0006915", "GO"), INCREASED), [
                member("m_c", None, slot("apoptosis", "GO_0006915", "GO"), INCREASED,
                       matched=False),
            ]),
        ],
    }
    ke2 = {"ke_id": "KE2", "title": "KE2", "gt": [], "clusters": [
        cl(2, ["m_a", "m_b"], (None, slot("cell death", "BAO_0000179", "BAO"), INCREASED), [
            member("m_a", None, slot("cell death", "BAO_0000179", "BAO"), INCREASED,
                   matched=False),
            member("m_b", None, slot("cell death", "BAO_0000179", "BAO"), INCREASED,
                   matched=False)])]}
    no_uuid = {"ke_id": "KE99", "title": "x", "gt": [], "clusters": [
        cl(2, ["m_a", "m_b"], (None, INFLAM, INCREASED), [])]}
    no_number = {"ke_id": "foo", "title": "x", "gt": [], "clusters": []}
    return {
        "products": "p",
        "summary": {"n_models": 3, "models": MODELS, "restricted": False, "n_clusters": 5,
                    "min_support": 2, "min_support_ensemble": 2, "tau": 0.75,
                    "embedding": "emb", "support_histogram": {"1": 1, "2": 3, "3": 1}},
        "kes": [ke10, ke2, no_uuid, no_number],
    }


def build(viz=None, min_support=2):
    report = kx.KecXmlReport()
    return kx.build_consensus(viz or make_viz(), KE_UUID, min_support, report), report


def write(tmp_path, viz=None, min_support=2, name="kec.xml"):
    doc, report = build(viz, min_support)
    return kx.write_xml(doc, tmp_path / name), report


def root_of(path):
    return etree.parse(str(path)).getroot()


def by_name(root):
    """term name -> list of term elements"""
    out = {}
    for t in root.iterfind("c:terms/c:term", NS):
        out.setdefault(t.findtext("c:name", namespaces=NS), []).append(t)
    return out


# ── round trip, validation ────────────────────────────────────────────────────

def test_round_trip_classes_to_xml_and_back(tmp_path):
    doc, _ = build()
    path = kx.write_xml(doc, tmp_path / "a.xml")
    parsed = XmlParser().from_path(path, KecConsensus)
    assert parsed.model_dump() == doc.model_dump()
    # Key Events by number: KE2 precedes KE10; support descending within the KE
    assert [t.ke_id for t in parsed.consensus_triple] == [KE_UUID["KE2"]] + [KE_UUID["KE10"]] * 2
    assert [t.support for t in parsed.consensus_triple[1:]] == [3, 2]
    assert [r.aop_wiki_id for r in parsed.lookup.key_event_reference] == [2, 10]


def test_output_validates_against_the_xsd_and_crosschecks(tmp_path):
    path, report = write(tmp_path)
    assert kx.validate(path, XSD, report) == []
    assert kx.crosscheck(path, report) == []
    assert report.validation_failures == 0 and report.crosscheck_failures == 0


def test_empty_document_is_valid(tmp_path):
    path, report = write(tmp_path, {"summary": {}, "kes": []})
    assert report.n_kes == 0 and report.n_clusters == 0
    assert kx.validate(path, XSD) == [] and kx.crosscheck(path) == []


def _mutate(tmp_path, pattern, repl, count=1):
    path, _ = write(tmp_path)
    bad = tmp_path / "bad.xml"
    text, n = re.subn(pattern, repl, path.read_text(encoding="utf-8"), count=count)
    assert n, pattern
    bad.write_text(text, encoding="utf-8")
    return bad


@pytest.mark.parametrize("pattern, repl", [
    (r'status="novel"', 'status="rejected"'),                      # enum
    (r'(<consensus-triple id=)"[^"]+"', r'\1"CT1"'),               # not a GUID
    (r'(<consensus-triple [^>]*?process-id=)"[^"]+"',
     r'\1"00000000-0000-5000-8000-000000000000"'),                 # dangling term (keyref)
    (r'(<consensus-triple [^>]*?ke-id=)"[^"]+"',
     r'\1"00000000-0000-5000-8000-000000000000"'),                 # KE missing from lookup
    (r'(<term [^>]*?kind=)"process"', r'\1"verb"'),                # term kind enum
    (r'(<bin support=)"1"', r'\1"-1"'),                            # nonNegativeInteger
])
def test_validation_rejects(tmp_path, pattern, repl):
    assert kx.validate(_mutate(tmp_path, pattern, repl), XSD)


def test_validation_rejects_status_that_contradicts_curated_indices(tmp_path):
    """XSD 1.1 assertion: confirmed <=> curated-indices present."""
    assert kx.validate(_mutate(tmp_path, r'status="confirmed" (support="3")',
                               r'status="novel" \1'), XSD)
    assert kx.validate(_mutate(tmp_path, r'(status="novel" support="2")',
                               r'\1 curated-indices="0"'), XSD)


def test_validation_rejects_duplicate_ids(tmp_path):
    path, _ = write(tmp_path)
    text = path.read_text(encoding="utf-8")
    ids = re.findall(r'<consensus-triple id="([^"]+)"', text)
    dup = tmp_path / "dup.xml"
    dup.write_text(text.replace(ids[1], ids[0]), encoding="utf-8")
    assert kx.validate(dup, XSD)                          # xs:unique on consensus-triple/@id
    terms = re.findall(r'<term id="([^"]+)"', text)
    dup.write_text(text.replace(terms[1], terms[0]), encoding="utf-8")
    assert kx.validate(dup, XSD)                          # xs:unique on term/@id


def test_crosscheck_catches_a_slot_pointing_at_a_term_of_another_kind(tmp_path):
    path, _ = write(tmp_path)
    root = root_of(path)
    ct = root.find("c:consensus-triple", NS)
    ct.set("object-id", ct.get("process-id"))           # object slot -> a process term
    path.write_bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))
    assert kx.validate(path, XSD) == []                 # the XSD cannot tell
    problems = kx.crosscheck(path)
    assert problems and "object-id" in problems[0]


def test_crosscheck_catches_wrong_counters(tmp_path):
    path, _ = write(tmp_path)
    root = root_of(path)
    root.find("c:key-event-result", NS).set("n-novel", "9")
    path.write_bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))
    assert any("n-novel" in p for p in kx.crosscheck(path))


def test_single_namespace_and_no_logprobs(tmp_path):
    path, _ = write(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert root_of(path).tag == f"{{{kx.KEC_NS}}}kec-consensus"
    assert "aop-xml" not in text and "logprob" not in text and "lp=" not in text


# ── content ───────────────────────────────────────────────────────────────────

def test_clusters_statuses_and_matches(tmp_path):
    path, report = write(tmp_path)
    root = root_of(path)
    cts = root.findall("c:consensus-triple", NS)
    assert [(c.get("status"), c.get("support")) for c in cts] == [
        ("novel", "2"), ("confirmed", "3"), ("novel", "2")]
    conf = cts[1]
    assert conf.get("models") == "m_a m_b m_c"
    assert conf.get("curated-indices") == "0"
    # best match as in the viz report: m_a (name) first, then the iri member replaces it
    assert (conf.get("method"), conf.get("score")) == ("iri", "1.0")
    assert cts[0].get("method") is None and cts[0].get("curated-indices") is None
    res = {r.get("ke-id"): r for r in root.findall("c:key-event-result", NS)}
    r10 = res[KE_UUID["KE10"]]
    assert [(c.get("index"), c.get("status")) for c in r10.findall("c:curated-triple", NS)] == [
        ("0", "confirmed"), ("1", "missed")]
    assert (r10.get("n-curated"), r10.get("n-confirmed"), r10.get("n-novel")) == ("2", "1", "1")
    assert report.n_curated_confirmed == 1 and report.n_curated_missed == 1
    assert (report.n_clusters_confirmed, report.n_clusters_novel) == (1, 2)


def test_curated_triples_are_referenced_not_duplicated(tmp_path):
    """A curated triple is its position + term references; the cluster points at it by
    position only."""
    path, _ = write(tmp_path)
    root = root_of(path)
    ct = root.find("c:key-event-result/c:curated-triple", NS)
    assert set(ct.attrib) == {"index", "status", "object-id", "process-id", "action-id"}
    cluster = root.findall("c:consensus-triple", NS)[1]
    assert cluster.get("curated-indices") == "0"
    assert cluster.find("c:curated-triple", NS) is None


def test_members_keep_each_models_own_triple_and_its_match(tmp_path):
    path, _ = write(tmp_path)
    root = root_of(path)
    terms = {t.get("id"): t for t in root.iterfind("c:terms/c:term", NS)}
    conf = root.findall("c:consensus-triple", NS)[1]
    ms = conf.findall("c:member", NS)
    assert [m.get("model") for m in ms] == ["m_a", "m_b", "m_c"]       # provenance
    # the representative has no object, but m_c's own triple does
    assert conf.get("object-id") is None
    assert terms[ms[2].get("object-id")].findtext("c:name", namespaces=NS) == "cell"
    assert ms[0].get("object-id") is None
    assert [(m.get("matched"), m.get("curated-index"), m.get("method")) for m in ms] == [
        ("true", "0", "name"), ("true", "0", "iri"), ("false", None, "none")]


def test_curated_index_only_when_matched():
    """The nearest curated triple of an unmatched member is not exported."""
    doc, _ = build()
    for ct in doc.consensus_triple:
        for m in ct.member:
            assert (m.curated_index is not None) == bool(m.matched)


def test_action_less_cluster_is_kept_valid_and_counted(tmp_path):
    path, report = write(tmp_path)
    assert report.clusters_without_action == 1 and report.members_without_action == 2
    assert report.clusters_without_object == 2 and report.clusters_without_process == 0
    novel = root_of(path).findall("c:consensus-triple", NS)[2]
    assert novel.get("action-id") is None and novel.get("object-id")
    assert kx.validate(path, XSD) == []


def test_stable_numbers_only_in_the_lookup(tmp_path):
    path, _ = write(tmp_path)
    root = root_of(path)
    refs = {r.get("id"): (r.get("aop-wiki-id"), r.get("title"))
            for r in root.iterfind("c:lookup/c:key-event-reference", NS)}
    # the title is omitted when the viz data only has the id as title
    assert refs == {KE_UUID["KE10"]: ("10", "Inflammation"), KE_UUID["KE2"]: ("2", None)}
    body = etree.tostring(root).decode()
    body = body[:body.index("<lookup")]
    assert not re.search(r"\bKE\d+\b", body) and "aop-wiki-id" not in body
    for el in root.iter(f"{{{kx.KEC_NS}}}consensus-triple", f"{{{kx.KEC_NS}}}key-event-result"):
        assert re.fullmatch(r"[0-9a-f-]{36}", el.get("ke-id"))


def test_summary_and_histogram(tmp_path):
    path, report = write(tmp_path)
    s = root_of(path).find("c:summary", NS)
    assert [m.text for m in s.findall("c:model", NS)] == MODELS
    assert dict(s.attrib) == {
        "n-models": "3", "restricted": "false", "n-kes": "2", "n-clusters": "5",
        "n-accepted": "3", "min-support": "2", "min-support-ensemble": "2",
        "tau": "0.75", "embedding": "emb"}
    # the histogram describes the whole comparison, accepted or not
    assert [(b.get("support"), b.get("clusters")) for b in s.iterfind("c:support-histogram/c:bin", NS)] \
        == [("1", "1"), ("2", "3"), ("3", "1")]


def test_histogram_is_computed_when_the_source_has_none(tmp_path):
    viz = make_viz()
    del viz["summary"]["support_histogram"]
    path, _ = write(tmp_path, viz)
    bins = [(b.get("support"), b.get("clusters"))
            for b in root_of(path).iterfind("c:summary/c:support-histogram/c:bin", NS)]
    assert bins == [("1", "1"), ("2", "3"), ("3", "1")]


# ── threshold, restricted ─────────────────────────────────────────────────────

def test_acceptance_threshold_is_a_parameter(tmp_path):
    _, strict = write(tmp_path, min_support=3, name="s.xml")
    _, loose = write(tmp_path, min_support=1, name="l.xml")
    assert (strict.n_clusters, strict.n_clusters_below_threshold) == (1, 3)
    assert (loose.n_clusters, loose.n_clusters_below_threshold) == (4, 0)
    # at 3 only the 3-model cluster stays, at 1 the below-threshold one is exported too
    assert (strict.n_clusters_confirmed, strict.n_clusters_novel) == (1, 0)
    assert (loose.n_clusters_confirmed, loose.n_clusters_novel) == (1, 3)
    assert strict.min_support_ensemble == 3


def test_threshold_decides_whether_a_cluster_confirms(tmp_path):
    viz = make_viz()
    viz["kes"][0]["clusters"][2]["members"][0]["match"].update(matched=True, ref_idx=1,
                                                               method="name")
    loose_path, loose = write(tmp_path, viz, min_support=1, name="l.xml")
    strict_path, strict = write(tmp_path, viz, min_support=2, name="s.xml")
    assert loose.n_curated_confirmed == 2 and strict.n_curated_confirmed == 1
    assert kx.validate(loose_path, XSD) == [] and kx.validate(strict_path, XSD) == []


def test_restricted_flag_is_carried_into_document_and_report(tmp_path):
    viz = make_viz()
    viz["summary"]["restricted"] = True
    path, report = write(tmp_path, viz)
    assert report.restricted is True and report.to_dict()["restricted"] is True
    assert root_of(path).find("c:summary", NS).get("restricted") == "true"
    _, unrestricted = write(tmp_path)
    assert unrestricted.restricted is False


# ── skipped Key Events ────────────────────────────────────────────────────────

def test_key_events_without_guid_or_number_are_listed_not_exported(tmp_path):
    path, report = write(tmp_path)
    assert report.kes_without_uuid == ["KE99"] and report.kes_without_number == ["foo"]
    assert (report.n_kes_in, report.n_kes) == (4, 2)
    assert report.n_clusters_in == 5
    assert "KE99" not in path.read_text(encoding="utf-8")


def test_key_event_with_nothing_to_show_is_counted_and_skipped(tmp_path):
    viz = make_viz()
    viz["kes"][1]["clusters"][0]["support"] = 1            # KE2: only a below-threshold cluster
    _, report = write(tmp_path, viz)
    assert report.kes_empty == 1 and report.n_kes == 1


def test_key_event_with_curated_triples_but_no_accepted_cluster_is_exported(tmp_path):
    viz = make_viz()
    for c in viz["kes"][0]["clusters"]:
        c["support"] = 1
    path, report = write(tmp_path, viz)
    res = [r for r in root_of(path).iterfind("c:key-event-result", NS)
           if r.get("ke-id") == KE_UUID["KE10"]][0]
    assert res.get("n-curated") == "2" and res.get("n-confirmed") == "0"
    assert report.n_curated_missed == 2
    assert kx.validate(path, XSD) == [] and kx.crosscheck(path) == []


def test_ke_uuids_from_kec_json_records():
    kec_a = {"KE5": {"ke_id": "KE5", "uuid": "u1", "title": "a"},
             "KE7": {"ke_id": "KE7", "uuid": "u2"},
             "KE8": {"ke_id": "KE8", "uuid": None},            # no GUID: not mapped
             "KE9": {"ke_id": "KE9"}}
    kec_b = {"KE8": {"ke_id": "KE8", "uuid": "u3"},            # filled in by the next model
             "KE5": {"ke_id": "KE5", "uuid": "u1"},
             "KE11": "not a record"}
    assert kx.ke_uuids_from_kec([kec_a, kec_b, {}]) == {"KE5": "u1", "KE7": "u2", "KE8": "u3"}


def test_ke_uuids_from_kec_falls_back_to_the_dict_key():
    assert kx.ke_uuids_from_kec([{"KE3": {"uuid": "u9"}}]) == {"KE3": "u9"}


def test_unresolved_key_events_within_the_limit_pass():
    report = kx.KecXmlReport(n_kes_in=100, kes_without_uuid=["KE1"] * 5)
    kx.check_unresolved(report)                                 # exactly 5% is allowed
    kx.check_unresolved(kx.KecXmlReport())                      # nothing read, nothing to check


def test_too_many_unresolved_key_events_fail_loudly():
    report = kx.KecXmlReport(n_kes_in=100, kes_without_uuid=[f"KE{i}" for i in range(6)])
    with pytest.raises(ValueError, match=r"6 of 100 .* no AOP-Wiki GUID.*KE0"):
        kx.check_unresolved(report)
    kx.check_unresolved(report, 0.10)                           # the limit is a parameter


def test_unresolved_check_on_a_built_consensus():
    _, report = build()                                         # KE99 of 4 has no GUID
    assert len(report.kes_without_uuid) == 1
    with pytest.raises(ValueError):
        kx.check_unresolved(report)                             # 25% > 5%
    kx.check_unresolved(report, 0.30)


def test_input_is_not_modified():
    viz = make_viz()
    before = copy.deepcopy(viz)
    build(viz)
    assert viz == before


# ── terms ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    # full IRI kept as given; id and ontology from its last segment
    (slot("x", GO, "GO"), ("GO", "GO_0006954", GO)),
    # the id's own prefix wins over a wrong or oddly cased `source`
    (slot("x", GO, "Go"), ("GO", "GO_0006954", GO)),
    (slot("x", "http://purl.obolibrary.org/obo/CHEBI_82594", "CHEBBI"),
     ("CHEBI", "CHEBI_82594", "http://purl.obolibrary.org/obo/CHEBI_82594")),
    # CURIE and short form: expanded with the prefix map of the assay export (OBO purl)
    (slot("x", "HP:0003281", "HP"), ("HP", "HP_0003281", "http://purl.obolibrary.org/obo/HP_0003281")),
    (slot("x", "GO_0006915", None), ("GO", "GO_0006915", "http://purl.obolibrary.org/obo/GO_0006915")),
    # BAO is not an OBO purl
    (slot("x", "BAO_0000179", "BAO"),
     ("BAO", "BAO_0000179", "http://www.bioassayontology.org/bao#BAO_0000179")),
    # the numeric AOP-Wiki action id has no prefix: ontology WIKI, no IRI
    (slot("increased", "1", "WIKI"), ("WIKI", "1", None)),
    # unknown prefix: id kept, no IRI invented
    (slot("x", "TAIR_123", "TAIR"), ("TAIR", "TAIR_123", None)),
    # a MeSH id has no underscore prefix: the slot's source is the ontology
    (slot("x", "D006965", "MeSH"), ("MESH", "D006965", None)),
    # no identifier at all, or "null" strings written by the extraction
    (slot("increased"), ("", "", None)),
    (slot("x", "null", "null"), ("", "", None)),
])
def test_normalise_slot(raw, expected):
    assert kx.normalise_slot(raw) == expected


def test_term_guid_is_deterministic_uuid5_and_separates_the_slot_kinds():
    a = kx.term_guid("process", "GO", "GO_0006954", "inflammatory response")
    assert a == kx.term_guid("process", "GO", "GO_0006954", "a different name")
    assert a == kx.term_guid("process", "GO", "go_0006954", "x")           # id case-folded
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", a)
    assert a != kx.term_guid("object", "GO", "GO_0006954", "inflammatory response")
    assert a != kx.term_guid("process", "PR", "GO_0006954", "inflammatory response")
    assert a != kx.term_guid("process", "GO", "GO_0006955", "inflammatory response")


def test_term_without_id_is_keyed_by_the_casefolded_name():
    a = kx.term_guid("action", "", "", "Increased")
    assert a == kx.term_guid("action", "", "", "  increased ")
    assert a != kx.term_guid("action", "", "", "decreased")
    assert a != kx.term_guid("action", "WIKI", "1", "Increased")          # id-less is separate
    assert a != kx.term_guid("object", "", "", "Increased")


def test_same_term_is_one_entry_everywhere(tmp_path):
    path, report = write(tmp_path)
    root = root_of(path)
    names = by_name(root)
    # GO inflammation is used by the curated triple, a representative, three members and a
    # second KE's cluster is a different term; one entry for the process term
    assert len(names["inflammatory response"]) == 1
    ferritin = names["ferritin"]
    # "ferritin" and "Ferritin" (ChEBI via a full IRI vs CHEBI_ short form) are one term; the
    # most frequent spelling is the name, the variant is counted
    assert len(ferritin) == 1 and report.terms_with_name_variants >= 1
    assert ferritin[0].get("source-id") == "CHEBI_82594"
    assert ferritin[0].get("iri") == "http://purl.obolibrary.org/obo/CHEBI_82594"
    # the curated action (WIKI 1) and the id-less model action are separate terms
    inc = sorted((t.get("source", ""), t.get("source-id", "")) for t in names["increased"])
    assert inc == [("", ""), ("WIKI", "1")]
    assert sum(report.n_terms.values()) == len(root.findall("c:terms/c:term", NS))


def test_term_without_iri_is_counted_by_kind_and_unknown_prefix_reported(tmp_path):
    viz = make_viz()
    viz["kes"][1]["clusters"][0]["rep"]["object"] = slot("protein", "TAIR_9", "TAIR")
    path, report = write(tmp_path, viz)
    assert report.unknown_prefixes == {"TAIR": 1}
    assert report.terms_without_iri["object"] == 1            # TAIR_9: prefix not in the map
    assert report.terms_without_iri["process"] == 1           # "serum ferritin": no id at all
    assert report.terms_without_iri["action"] == 3            # WIKI 1, WIKI 2 and id-less
    t = by_name(root_of(path))["protein"][0]
    assert t.get("iri") is None and t.get("source-id") == "TAIR_9"
    assert kx.validate(path, XSD) == []


# ── determinism ───────────────────────────────────────────────────────────────

def _shuffled(viz, seed):
    rnd = random.Random(seed)
    v = copy.deepcopy(viz)
    rnd.shuffle(v["kes"])
    for k in v["kes"]:
        rnd.shuffle(k["clusters"])
        for c in k["clusters"]:
            rnd.shuffle(c["members"])
    return v


def test_output_is_independent_of_input_order_and_repeatable(tmp_path):
    ref = write(tmp_path, name="ref.xml")[0].read_text(encoding="utf-8")
    assert write(tmp_path, name="again.xml")[0].read_text(encoding="utf-8") == ref
    for seed in range(5):
        out = write(tmp_path, _shuffled(make_viz(), seed), name=f"s{seed}.xml")[0]
        assert out.read_text(encoding="utf-8") == ref


def test_cluster_guid_is_deterministic_and_depends_on_ke_and_representative():
    rep = ("o", "p", None)
    g = kx.cluster_guid(KE_UUID["KE10"].upper(), rep)
    assert g == kx.cluster_guid(KE_UUID["KE10"], rep)
    assert g != kx.cluster_guid(KE_UUID["KE2"], rep)
    assert g != kx.cluster_guid(KE_UUID["KE10"], ("o", "p", "a"))
    assert g != kx.cluster_guid(KE_UUID["KE10"], rep, ordinal=2)
    assert g != kx.term_guid("object", "", "", "o")


def test_clusters_with_the_same_representative_get_distinct_ids_and_are_counted(tmp_path):
    viz = make_viz()
    clusters = viz["kes"][0]["clusters"]
    twin = copy.deepcopy(clusters[0])
    twin["support"] = 2
    clusters.append(twin)
    path, report = write(tmp_path, viz)
    ids = [c.get("id") for c in root_of(path).iterfind("c:consensus-triple", NS)]
    assert len(ids) == len(set(ids)) == 4
    assert report.guid_collisions == 1
    assert kx.validate(path, XSD) == []


def test_illegal_control_characters_are_stripped(tmp_path):
    viz = make_viz()
    viz["kes"][0]["title"] = "Inflam\x0bmation"
    viz["kes"][0]["clusters"][0]["rep"]["process"] = slot("inflam\x00matory response", GO, "GO")
    path, report = write(tmp_path, viz)
    assert report.stripped_control_chars >= 2
    assert kx.validate(path, XSD) == []


# ── HTML view ─────────────────────────────────────────────────────────────────

def test_html_view_mirrors_the_viz_report(tmp_path):
    path, _ = write(tmp_path)
    html = kx.render_html(path, XSL, tmp_path / "out" / "kec.html")
    text = html.read_text(encoding="utf-8")
    # Key Events by number from the lookup, with title; GUIDs are not shown
    assert "KE10" in text and "KE2" in text and "Inflammation" in text
    assert KE_UUID["KE10"] not in text
    assert "https://aopwiki.org/events/10" in text
    # counts per KE and the three row kinds
    assert re.search(r"1/2\s+curated confirmed", text) and "1 novel" in text
    assert 'class="gt-row"' in text and 'class="miss-row"' in text and 'class="novel-row"' in text
    assert "not found by any model" in text and "confirmed (1 cluster(s))" in text
    # confirming cluster: support/n_models, models, best match; member provenance
    assert "3/3" in text and "m_a,m_b,m_c" in text and 'tier tier-iri' in text
    assert "3 member triple(s)" in text
    # terms link to the OLS by their short id; header summary and histogram
    assert "https://www.ebi.ac.uk/ols4/search?q=GO_0006954" in text
    assert re.search(r"3\s+accepted \(support &gt;= 2\)", text)
    assert "sup=1: 1" in text and 'class="bin dropped"' in text
