"""Tests for assay-name consolidation (tasks/assay_names.py)."""
import json

import pytest

from tasks.assay_names import build_synonym_map, punct_normalize, resolve


def test_missing_synonym_file_is_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_synonym_map(str(tmp_path / "nope.json"))


def test_synonym_map_groups_variants(tmp_path):
    f = tmp_path / "syn.json"
    f.write_text(json.dumps({"Western blot": ["Western-blotting", "immunoblot"]}),
                 encoding="utf-8")
    m = build_synonym_map(str(f))
    keys = {resolve(n, m) for n in ("Western blot", "western  blotting", "Immunoblot")}
    assert keys == {"western blot"}
    assert resolve("ELISA", m) == "elisa"  # unmapped names keep their punct-normalised key


def test_punct_normalize():
    assert punct_normalize(" RT-PCR (qPCR)/x ") == "rt pcr qpcr x"


def test_shipped_synonym_file_exists_and_loads():
    m = build_synonym_map("resources/assay_synonyms.json")
    assert m




# ── permanent sequential ids: ASSAY<n> (record identity) and TECH<n> (group key) ──────

import pytest  # noqa: E402

from tasks.assay_names import (assay_identity, assign_ids, assign_technique_ids,  # noqa: E402
                               resolve_key,
                               ke_assay_id, load_id_registry, technique_id,
                               write_id_registry)

RECS = [("KE10", "Hestrin method", "acetylcholine levels"),
        ("KE10", "Hestrin method", "esterase activity"),    # same KE + technique
        ("KE11", "Ellman assay", "thiols")]


def assign(recs, registry):
    assign_ids([assay_identity(*r) for r in recs], registry)
    return [ke_assay_id(registry, *r) for r in recs]


def test_first_run_numbers_start_at_one_and_are_unique():
    reg = {}
    ids = assign(RECS, reg)
    assert sorted(ids) == ["ASSAY1", "ASSAY2", "ASSAY3"] and len(set(ids)) == 3
    assert ids[0] != ids[1]          # same KE + same technique, different endpoints


def test_numbers_do_not_depend_on_record_order():
    a, b = {}, {}
    assert dict(zip(RECS, assign(RECS, a))) == dict(zip(RECS[::-1], assign(RECS[::-1], b)))


def test_rerun_is_stable_and_normalisation_insensitive():
    reg = {}
    first = assign(RECS, reg)
    assert assign(RECS, reg) == first and len(reg) == 3
    assert ke_assay_id(reg, "KE10", " hestrin  METHOD ", "Acetylcholine levels") == first[0]


def test_new_assay_gets_max_plus_one_and_existing_keep_numbers():
    reg = {}
    first = assign(RECS, reg)
    ids = assign([("KE12", "Comet assay", "DNA breaks")] + RECS, reg)   # new one listed FIRST
    assert ids[1:] == first and ids[0] == "ASSAY4"


def test_numbers_are_never_reused_after_an_identity_disappears():
    reg = {}
    assign(RECS, reg)
    assign(RECS[1:], reg)                              # first assay dropped from the run
    assert assign([("KE13", "New one", "x")], reg) == ["ASSAY4"] and len(reg) == 4


def test_assay_id_is_independent_of_technique_grouping():
    import inspect
    assert list(inspect.signature(ke_assay_id).parameters) == [
        "registry", "ke_id", "assay_name", "measured_endpoint"]


def test_records_sharing_an_identity_share_an_id():
    """Reported as a collision by assay_consensus2solr, not papered over."""
    reg = {}
    ids = assign([RECS[0], RECS[0]], reg)
    assert ids[0] == ids[1] and len(reg) == 1


def test_registry_round_trip_missing_file_and_atomic_write(tmp_path):
    p = tmp_path / "res" / "assay_ids.json"
    assert load_id_registry(p) == {}                   # absent -> empty, starts at 1
    reg = {}
    assign(RECS, reg)
    write_id_registry(reg, p)
    assert load_id_registry(p) == reg
    assert not list(p.parent.glob("*.tmp"))            # temp file replaced, not left behind
    again = load_id_registry(p)
    assign(RECS + [("KE14", "z", "w")], again)
    assert again["KE14|z|w"] == 4


def test_registry_loader_rejects_duplicate_numbers_and_non_positive(tmp_path):
    p = tmp_path / "ids.json"
    p.write_text(json.dumps({"a": 1, "b": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="more than one"):
        load_id_registry(p)
    p.write_text(json.dumps({"a": 0}), encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        load_id_registry(p)


def test_failed_write_leaves_the_old_registry_intact(tmp_path, monkeypatch):
    p = tmp_path / "ids.json"
    write_id_registry({"a": 1}, p)
    monkeypatch.setattr("tasks.assay_names.os.replace",
                        lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        write_id_registry({"a": 1, "b": 2}, p)
    assert load_id_registry(p) == {"a": 1}
    assert not list(tmp_path.glob("*.tmp"))            # no leftover temp file


# techniques --------------------------------------------------------------------
def tech(group_keys, reg, smap=None):
    return assign_technique_ids(group_keys, reg, smap or {})


def test_technique_ids_sequential_stable_and_order_independent():
    reg = {}
    n1, r1 = tech(["western blot", "elisa", "mtt"], reg)
    n2, _ = tech(["mtt", "elisa", "western blot"], reg)
    assert n1 == n2 == {"elisa": 1, "mtt": 2, "western blot": 3} and r1 == {}
    assert technique_id(n1["mtt"]) == "TECH2"
    n3, _ = tech(["comet", "elisa"], reg)                        # new = max + 1, old kept
    assert n3 == {"comet": 4, "elisa": 1}


def test_technique_number_survives_a_new_variant_spelling():
    reg = {}
    before, _ = tech(["western blot"], reg)
    smap = {"western blotting": "western blot", "western blot": "western blot"}
    after, retired = tech([resolve("Western-blotting", smap)], reg, smap)
    assert before == after == {"western blot": 1} and len(reg) == 1 and retired == {}


def test_merge_keeps_the_canonicals_number_and_reports_the_absorbed_one():
    """x=1 and y=2 are registered; the map folds x into y. y keeps 2; x's number 1 is
    reported as absorbed (references to it go stale), the registry is untouched."""
    reg = {}
    tech(["x assay", "y assay"], reg)
    smap = {"x assay": "y assay", "y assay": "y assay"}
    numbers, retired = tech(["y assay"], reg, smap)
    assert numbers == {"y assay": 2} and retired == {1: 2}
    assert reg == {"x assay": 1, "y assay": 2}                  # append-only, unchanged
    assert tech(["y assay"], reg, smap) == ({"y assay": 2}, {1: 2})   # idempotent
    assert tech(["y assay", "z assay"], reg, smap)[0] == {"y assay": 2, "z assay": 3}


def test_reversing_the_merge_direction_is_an_explicit_reported_change():
    reg = {}
    tech(["x", "y"], reg)                                         # x=1, y=2
    assert tech(["x"], reg, {"y": "x", "x": "x"}) == ({"x": 1}, {2: 1})
    assert tech(["y"], reg, {"x": "y", "y": "y"}) == ({"y": 2}, {1: 2})
    assert reg == {"x": 1, "y": 2}


def test_new_canonical_gets_a_fresh_number_and_aliases_map_to_it():
    reg = {"a": 1, "b": 2}
    smap = {"a": "c", "b": "c", "c": "c"}
    numbers, retired = tech(["c"], reg, smap)
    assert numbers == {"c": 3} and retired == {1: 3, 2: 3}
    assert reg == {"a": 1, "b": 2, "c": 3}                        # nothing removed


def test_unmerge_restores_the_old_numbers_reviewer_probe():
    reg = {"a": 1, "b": 2}
    smap = {"a": "c", "b": "c", "c": "c"}
    tech(["c"], reg, smap)                                        # merge into new canonical
    numbers, retired = tech(["a", "b"], reg)                      # un-merge
    assert numbers == {"a": 1, "b": 2} and retired == {}
    assert reg == {"a": 1, "b": 2, "c": 3}


def test_merge_unmerge_remerge_is_consistent():
    reg = {}
    tech(["x", "y"], reg)
    smap = {"x": "y", "y": "y"}
    first = tech(["y"], reg, smap)
    assert tech(["x", "y"], reg)[0] == {"x": 1, "y": 2}           # un-merge: own numbers back
    assert tech(["y"], reg, smap) == first == ({"y": 2}, {1: 2})  # re-merge: same result
    assert reg == {"x": 1, "y": 2}


def test_alias_chains_resolve_transitively():
    smap = {"w": "x", "x": "y", "y": "y"}
    assert resolve_key("w", smap) == "y" and resolve_key("y", smap) == "y"
    assert resolve("W", smap) == "y"


def test_synonym_map_cycle_is_a_clear_error():
    with pytest.raises(ValueError, match="cycle"):
        resolve_key("a", {"a": "b", "b": "a"})


def test_three_key_chain_scenario_never_gives_two_techniques_one_number():
    """Review scenario: x, y registered; map {w->x, x->y, y->y} (x is both a variant and
    a canonical elsewhere). Chains resolve to y, so x is absorbed into y and the two never
    share a number."""
    reg = {"x": 1, "y": 2}
    smap = {"w": "x", "x": "y", "y": "y"}
    numbers, retired = tech(["y"], reg, smap)
    assert numbers == {"y": 2} and retired == {1: 2}
    assert len(set(numbers.values())) == len(numbers)


def test_two_live_group_keys_sharing_a_number_raise():
    # defensive guard: a registry that bypassed the loader check and holds one number twice
    with pytest.raises(ValueError, match="shared by several group keys"):
        assign_technique_ids(["x", "y"], {"x": 1, "y": 1}, {})
