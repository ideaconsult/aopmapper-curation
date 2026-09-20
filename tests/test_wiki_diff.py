"""Unit tests for the wiki-release diff logic (tasks/wiki_diff.py).

KE and term models are plain attribute-access objects (SimpleNamespace), matching
the shape of the generated pydantic models (aopkb.Data.KeyEvent etc.) closely enough
for get_kid()/diff_* to operate without needing a real parsed XML release.
"""
from types import SimpleNamespace

from tasks.wiki_diff import (
    diff_kes, diff_terms, diff_release, triple_wiki_ids, term_to_kes,
    ke_report_rows, term_report_rows, render_markdown, KE_REPORT_COLUMNS,
)


def term(id, name, source=None, source_id=None):
    return SimpleNamespace(id=id, name=name, source=source, source_id=source_id)


def biological_event(object_id=None, process_id=None, action_id=None):
    return SimpleNamespace(object_id=object_id, process_id=process_id, action_id=action_id)


def key_event(id, title, events=None, **fields):
    bio = SimpleNamespace(biological_event=events) if events is not None else None
    defaults = dict(
        short_name=title, biological_organization_level=None, description="",
        measurement_methodology=None, evidence_supporting_taxonomic_applicability=None,
        associated_tests=None, references=None,
    )
    defaults.update(fields)
    return SimpleNamespace(id=id, title=title, biological_events=bio, **defaults)


def make_data(kes=(), objects=(), processes=(), actions=()):
    return SimpleNamespace(
        key_event=list(kes), biological_object=list(objects),
        biological_process=list(processes), biological_action=list(actions),
    )


def make_lookup(kes=(), objects=(), processes=(), actions=()):
    """uuid -> {"wiki-id": ...} lookup, qname-keyed the way get_kid() expects."""
    lookup = {"key-event-reference": {}, "biological-object-reference": {},
              "biological-process-reference": {}, "biological-action-reference": {}}
    for uuid, wid in kes:
        lookup["key-event-reference"][uuid] = {"wiki-id": wid}
    for uuid, wid in objects:
        lookup["biological-object-reference"][uuid] = {"wiki-id": wid}
    for uuid, wid in processes:
        lookup["biological-process-reference"][uuid] = {"wiki-id": wid}
    for uuid, wid in actions:
        lookup["biological-action-reference"][uuid] = {"wiki-id": wid}
    return lookup


# ── diff_terms ──────────────────────────────────────────────────────────────

def test_diff_terms_added_removed_unchanged():
    old_data = make_data(objects=[term("u1", "liver"), term("u2", "kidney")])
    new_data = make_data(objects=[term("u1", "liver"), term("u3", "heart")])
    lookup = make_lookup(objects=[("u1", "UBERON_1"), ("u2", "UBERON_2"), ("u3", "UBERON_3")])

    d = diff_terms(old_data, lookup, new_data, lookup, "biological_object")

    assert d["n_old"] == 2 and d["n_new"] == 2
    assert d["added"] == [{"wiki_id": "UBERON_3", "name": "heart", "ke_ids": []}]
    assert d["removed"] == [{"wiki_id": "UBERON_2", "name": "kidney", "ke_ids": []}]
    assert d["changed"] == {}


def test_diff_terms_changed_name():
    old_data = make_data(objects=[term("u1", "liver", source="UBERON", source_id="UBERON:1")])
    new_data = make_data(objects=[term("u1", "hepatic tissue", source="UBERON", source_id="UBERON:1")])
    lookup = make_lookup(objects=[("u1", "UBERON_1")])

    d = diff_terms(old_data, lookup, new_data, lookup, "biological_object")

    assert d["added"] == [] and d["removed"] == []
    assert d["changed"]["UBERON_1"]["fields"]["name"] == {"old": "liver", "new": "hepatic tissue"}


def test_diff_terms_skips_ids_without_wiki_id():
    # a term whose uuid has no lookup entry is dropped, not KeyError'd
    old_data = make_data(objects=[term("u1", "liver")])
    new_data = make_data(objects=[term("u1", "liver")])
    empty_lookup = make_lookup()

    d = diff_terms(old_data, empty_lookup, new_data, empty_lookup, "biological_object")

    assert d["n_old"] == 0 and d["n_new"] == 0


# ── term_to_kes ───────────────────────────────────────────────────────────────

def test_term_to_kes_maps_term_to_referencing_kes():
    lookup = make_lookup(kes=[("k1", "KE1"), ("k2", "KE2")],
                          objects=[("o1", "OBJ1")], processes=[("p1", "PROC1")],
                          actions=[("a1", "increased")])
    ke1 = key_event("k1", "KE one", events=[biological_event("o1", "p1", "a1")])
    ke2 = key_event("k2", "KE two", events=[biological_event("o1", "p1", "a1")])
    data = make_data(kes=[ke1, ke2], objects=[term("o1", "obj")],
                      processes=[term("p1", "proc")], actions=[term("a1", "increased")])

    idx = term_to_kes(data, lookup)

    assert idx["biological_object"]["OBJ1"] == {"KE1", "KE2"}
    assert idx["biological_process"]["PROC1"] == {"KE1", "KE2"}
    assert idx["biological_action"]["increased"] == {"KE1", "KE2"}


def test_term_to_kes_ignores_kes_without_biological_events():
    lookup = make_lookup(kes=[("k1", "KE1")])
    ke1 = key_event("k1", "KE one", events=None)
    data = make_data(kes=[ke1])

    idx = term_to_kes(data, lookup)

    assert idx["biological_object"] == {}


# ── diff_terms KE attribution (via diff_release) ─────────────────────────────

def test_diff_release_attributes_added_term_to_its_kes():
    lookup = make_lookup(kes=[("k1", "KE1")], objects=[("o1", "OBJ1")],
                          processes=[("p1", "PROC1")], actions=[("a1", "increased")])
    old_data = make_data(kes=[key_event("k1", "KE one", events=[])])
    new_ke = key_event("k1", "KE one", events=[biological_event("o1", "p1", "a1")])
    new_data = make_data(kes=[new_ke], objects=[term("o1", "obj")],
                          processes=[term("p1", "proc")], actions=[term("a1", "increased")])

    report = diff_release(old_data, lookup, new_data, lookup, "A", "B")

    assert report["biological_object"]["added"] == [{"wiki_id": "OBJ1", "name": "obj", "ke_ids": ["KE1"]}]


def test_diff_release_attributes_removed_term_to_its_old_kes():
    lookup = make_lookup(kes=[("k1", "KE1")], objects=[("o1", "OBJ1")],
                          processes=[("p1", "PROC1")], actions=[("a1", "increased")])
    old_ke = key_event("k1", "KE one", events=[biological_event("o1", "p1", "a1")])
    old_data = make_data(kes=[old_ke], objects=[term("o1", "obj")],
                          processes=[term("p1", "proc")], actions=[term("a1", "increased")])
    new_data = make_data(kes=[key_event("k1", "KE one", events=[])])

    report = diff_release(old_data, lookup, new_data, lookup, "A", "B")

    assert report["biological_object"]["removed"] == [{"wiki_id": "OBJ1", "name": "obj", "ke_ids": ["KE1"]}]


def test_render_markdown_shows_ke_ids_for_term_changes():
    lookup = make_lookup(kes=[("k1", "KE1")], objects=[("o1", "OBJ1")],
                          processes=[("p1", "PROC1")], actions=[("a1", "increased")])
    old_data = make_data(kes=[key_event("k1", "KE one", events=[])])
    new_ke = key_event("k1", "KE one", events=[biological_event("o1", "p1", "a1")])
    new_data = make_data(kes=[new_ke], objects=[term("o1", "obj")],
                          processes=[term("p1", "proc")], actions=[term("a1", "increased")])

    report = diff_release(old_data, lookup, new_data, lookup, "A", "B")
    md = render_markdown(report)

    assert "(KEs: KE1)" in md


# ── triple_wiki_ids ───────────────────────────────────────────────────────────

def test_triple_wiki_ids_resolves_each_slot():
    ke = key_event("ke1", "Some KE", events=[biological_event("o1", "p1", "a1")])
    lookup = make_lookup(objects=[("o1", "OBJ")], processes=[("p1", "PROC")], actions=[("a1", "increased")])

    triples = triple_wiki_ids(ke, lookup)

    assert triples == {("OBJ", "PROC", "increased")}


def test_triple_wiki_ids_no_biological_events_is_empty():
    ke = key_event("ke1", "Some KE", events=None)
    assert triple_wiki_ids(ke, make_lookup()) == set()


# ── diff_kes ──────────────────────────────────────────────────────────────────

def test_diff_kes_added_and_removed():
    old_data = make_data(kes=[key_event("u1", "KE one")])
    new_data = make_data(kes=[key_event("u1", "KE one"), key_event("u2", "KE two")])
    lookup = make_lookup(kes=[("u1", "KE1"), ("u2", "KE2")])

    d = diff_kes(old_data, lookup, new_data, lookup)

    assert d["added"] == [{"wiki_id": "KE2", "title": "KE two",
                           "kec_triples_added": [], "kec_triples_removed": []}]
    assert d["removed"] == []
    assert d["changed"] == {}


def test_diff_kes_added_ke_reports_its_triples():
    """A KE new to the release brings all of its triples with it; they must be counted."""
    lookup = make_lookup(kes=[("u1", "KE1"), ("u2", "KE2")], objects=[("o1", "OBJ1")],
                          processes=[("p1", "PROC1")], actions=[("a1", "increased")])
    old_data = make_data(kes=[key_event("u1", "KE one", events=[])])
    new_ke = key_event("u2", "KE two", events=[biological_event("o1", "p1", "a1")])
    new_data = make_data(kes=[key_event("u1", "KE one", events=[]), new_ke])

    d = diff_kes(old_data, lookup, new_data, lookup)

    assert d["added"][0]["kec_triples_added"] == [
        {"object": "OBJ1", "process": "PROC1", "action": "increased"}]
    assert d["n_triples_old"] == 0 and d["n_triples_new"] == 1
    assert d["n_triples_added"] == 1 and d["n_triples_removed"] == 0


def test_diff_kes_removed_ke_reports_its_triples():
    lookup = make_lookup(kes=[("u1", "KE1")], objects=[("o1", "OBJ1")],
                          processes=[("p1", "PROC1")], actions=[("a1", "increased")])
    old_ke = key_event("u1", "KE one", events=[biological_event("o1", "p1", "a1")])
    old_data = make_data(kes=[old_ke])
    new_data = make_data(kes=[])

    d = diff_kes(old_data, lookup, new_data, lookup)

    assert d["removed"][0]["kec_triples_removed"] == [
        {"object": "OBJ1", "process": "PROC1", "action": "increased"}]
    assert d["n_triples_old"] == 1 and d["n_triples_new"] == 0
    assert d["n_triples_added"] == 0 and d["n_triples_removed"] == 1


def test_diff_kes_triple_totals_span_added_and_changed_kes():
    """The regression this guards: totals computed only over `changed` omit new KEs."""
    lookup = make_lookup(kes=[("u1", "KE1"), ("u2", "KE2")],
                          objects=[("o1", "OBJ1"), ("o2", "OBJ2")],
                          processes=[("p1", "PROC1")], actions=[("a1", "increased")])
    old_data = make_data(kes=[key_event("u1", "KE one", events=[])])
    new_data = make_data(kes=[
        key_event("u1", "KE one", events=[biological_event("o1", "p1", "a1")]),   # changed
        key_event("u2", "KE two", events=[biological_event("o2", "p1", "a1")]),   # added
    ])

    d = diff_kes(old_data, lookup, new_data, lookup)

    assert d["n_triples_added"] == 2   # one on the changed KE, one on the added KE
    assert d["n_triples_old"] == 0 and d["n_triples_new"] == 2


def test_diff_kes_field_change_reported():
    old_data = make_data(kes=[key_event("u1", "KE one", description="old desc")])
    new_data = make_data(kes=[key_event("u1", "KE one", description="new desc")])
    lookup = make_lookup(kes=[("u1", "KE1")])

    d = diff_kes(old_data, lookup, new_data, lookup)

    assert d["changed"]["KE1"]["fields"]["description"] == {"old": "old desc", "new": "new desc"}
    assert d["changed"]["KE1"]["kec_triples_added"] == []
    assert d["changed"]["KE1"]["kec_triples_removed"] == []


def test_diff_kes_kec_triple_added_and_removed():
    obj_lookup = [("o1", "OBJ1"), ("o2", "OBJ2")]
    proc_lookup = [("p1", "PROC1")]
    act_lookup = [("a1", "increased")]
    lookup = make_lookup(kes=[("u1", "KE1")], objects=obj_lookup, processes=proc_lookup, actions=act_lookup)

    old_ke = key_event("u1", "KE one", events=[biological_event("o1", "p1", "a1")])
    new_ke = key_event("u1", "KE one", events=[biological_event("o2", "p1", "a1")])
    old_data = make_data(kes=[old_ke])
    new_data = make_data(kes=[new_ke])

    d = diff_kes(old_data, lookup, new_data, lookup)

    changed = d["changed"]["KE1"]
    assert changed["kec_triples_added"] == [{"object": "OBJ2", "process": "PROC1", "action": "increased"}]
    assert changed["kec_triples_removed"] == [{"object": "OBJ1", "process": "PROC1", "action": "increased"}]
    assert changed["fields"] == {}  # only the triple changed, not KE text fields


def test_diff_kes_unchanged_ke_not_reported():
    lookup = make_lookup(kes=[("u1", "KE1")], objects=[("o1", "OBJ1")],
                          processes=[("p1", "PROC1")], actions=[("a1", "increased")])
    events = [biological_event("o1", "p1", "a1")]
    old_data = make_data(kes=[key_event("u1", "KE one", events=events)])
    new_data = make_data(kes=[key_event("u1", "KE one", events=events)])

    d = diff_kes(old_data, lookup, new_data, lookup)

    assert d["changed"] == {}


# ── diff_release ────────────────────────────────────────────────────────────

def test_diff_release_bundles_all_categories():
    lookup = make_lookup(kes=[("u1", "KE1")])
    old_data = make_data(kes=[key_event("u1", "old title")])
    new_data = make_data(kes=[key_event("u1", "new title")])

    report = diff_release(old_data, lookup, new_data, lookup, version_old="A", version_new="B")

    assert report["version_old"] == "A" and report["version_new"] == "B"
    assert set(report) == {"version_old", "version_new", "key_event",
                            "biological_object", "biological_process", "biological_action"}
    assert report["key_event"]["changed"]["KE1"]["fields"]["title"] == {"old": "old title", "new": "new title"}


# ── ke_report_rows / render_markdown ─────────────────────────────────────────

def test_ke_report_rows_covers_added_removed_changed():
    lookup = make_lookup(kes=[("u1", "KE1"), ("u2", "KE2"), ("u3", "KE3")])
    old_data = make_data(kes=[
        key_event("u1", "KE one", description="old desc"),
        key_event("u2", "KE two"),
    ])
    new_data = make_data(kes=[
        key_event("u1", "KE one", description="new desc"),
        key_event("u3", "KE three"),
    ])
    report = diff_release(old_data, lookup, new_data, lookup, "A", "B")

    rows = ke_report_rows(report)
    by_status = {r["status"]: r for r in rows}

    assert by_status["added"]["wiki_id"] == "KE3"
    assert by_status["removed"]["wiki_id"] == "KE2"
    assert set(KE_REPORT_COLUMNS) == set(rows[0])
    changed = by_status["changed"]
    assert changed["wiki_id"] == "KE1"
    assert changed["changed_fields"] == "description"
    assert changed["description_old"] == "old desc"
    assert changed["description_new"] == "new desc"
    assert changed["measurement_methodology_old"] == ""


def test_ke_report_rows_kec_triples_formatted():
    lookup = make_lookup(kes=[("u1", "KE1")], objects=[("o1", "OBJ1")],
                          processes=[("p1", "PROC1")], actions=[("a1", "increased")])
    old_ke = key_event("u1", "KE one", events=[])
    new_ke = key_event("u1", "KE one", events=[biological_event("o1", "p1", "a1")])
    report = diff_release(make_data(kes=[old_ke]), lookup, make_data(kes=[new_ke]), lookup, "A", "B")

    row = ke_report_rows(report)[0]
    assert row["kec_triples_added"] == "OBJ1 / PROC1 / increased"
    assert row["kec_triples_removed"] == ""


def test_term_report_rows_covers_all_categories_with_ke_ids():
    lookup = make_lookup(kes=[("k1", "KE1")], objects=[("o1", "OBJ1")],
                          processes=[("p1", "PROC1")], actions=[("a1", "increased")])
    old_data = make_data(kes=[key_event("k1", "KE one", events=[])])
    new_ke = key_event("k1", "KE one", events=[biological_event("o1", "p1", "a1")])
    new_data = make_data(kes=[new_ke], objects=[term("o1", "obj")],
                          processes=[term("p1", "proc")], actions=[term("a1", "increased")])
    report = diff_release(old_data, lookup, new_data, lookup, "A", "B")

    rows = term_report_rows(report)
    by_category = {r["category"]: r for r in rows}

    assert by_category["biological_object"] == {
        "category": "biological_object", "status": "added", "wiki_id": "OBJ1",
        "name": "obj", "changed_fields": "", "ke_ids": "KE1",
    }
    assert by_category["biological_process"]["wiki_id"] == "PROC1"
    assert by_category["biological_action"]["wiki_id"] == "increased"


def test_render_markdown_mentions_changed_ke_and_fields():
    lookup = make_lookup(kes=[("u1", "KE1")])
    old_data = make_data(kes=[key_event("u1", "old title", description="old desc")])
    new_data = make_data(kes=[key_event("u1", "new title", description="new desc")])
    report = diff_release(old_data, lookup, new_data, lookup, "A", "B")

    md = render_markdown(report)

    assert "A -> B" in md
    assert "KE1" in md
    assert "old desc" in md and "new desc" in md


def test_render_markdown_toc_links_to_changed_ke_heading():
    lookup = make_lookup(kes=[("u1", "KE1")])
    old_data = make_data(kes=[key_event("u1", "old title", description="old desc")])
    new_data = make_data(kes=[key_event("u1", "new title", description="new desc")])
    report = diff_release(old_data, lookup, new_data, lookup, "A", "B")

    md = render_markdown(report)

    assert "## Contents" in md
    # the KE subsection heading and its TOC anchor link must agree
    assert "### KE1 — new title" in md
    assert "(#ke1-new-title)" in md
    toc_line = next(l for l in md.splitlines() if "[KE1" in l)
    assert toc_line.startswith("  - [")  # nested under "KEs changed"


def test_render_markdown_reports_triples_for_added_kes_and_totals():
    lookup = make_lookup(kes=[("u1", "KE1"), ("u2", "KE2")], objects=[("o1", "OBJ1")],
                          processes=[("p1", "PROC1")], actions=[("a1", "increased")])
    old_data = make_data(kes=[key_event("u1", "KE one")])
    new_ke = key_event("u2", "KE two", events=[biological_event("o1", "p1", "a1")])
    new_data = make_data(kes=[key_event("u1", "KE one"), new_ke])
    report = diff_release(old_data, lookup, new_data, lookup, "A", "B")

    md = render_markdown(report)

    assert "KEC triples: 0 -> 1 (+1 / -0" in md
    assert "OBJ1 / PROC1 / increased" in md


def test_render_markdown_skips_empty_term_sections():
    lookup = make_lookup(kes=[("u1", "KE1")])
    old_data = make_data(kes=[key_event("u1", "same title")])
    new_data = make_data(kes=[key_event("u1", "same title")])
    report = diff_release(old_data, lookup, new_data, lookup, "A", "B")

    md = render_markdown(report)

    assert "Biological Object" not in md
