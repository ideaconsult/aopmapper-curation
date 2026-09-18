"""Unit tests for tasks/ontology_ground.py — the pure grounding logic (no network)."""
from tasks.ontology_ground import (
    ground_name, _iri, normalise_iri, build_aopwiki_index,
)


def doc(names, ontology, prefix="http://x.org", ident="T_1"):
    return {"name": names, "ontology": [ontology], "prefix": [prefix], "id": ident}


def test_iri_built_from_prefix_and_id():
    assert _iri(doc(["x"], "GO", "http://purl.obolibrary.org/obo", "GO_0006915")) \
        == "http://purl.obolibrary.org/obo/GO_0006915"


def test_iri_none_when_missing():
    assert _iri({"name": ["x"]}) is None


def test_exact_match_wins_over_partial():
    docs = [doc(["Apoptosis Pathway"], "NCIT", ident="C1"),
            doc(["Apoptosis", "apoptosis"], "GO", ident="GO_0006915")]
    g = ground_name("apoptosis", docs)
    assert g["match"] == "exact"
    assert g["ontology"] == "GO"            # the exact one, not the partial NCIT
    # the matched synonym in the ontology's own casing (not the partial primary label)
    assert g["suggested_name"] == "Apoptosis"


def test_no_exact_is_fuzzy():
    docs = [doc(["Glucose Plasma Concentration"], "CSEO", ident="C2")]
    g = ground_name("plasma concentration", docs)
    assert g["match"] == "fuzzy"
    assert g["suggested_name"] == "Glucose Plasma Concentration"


def test_no_docs_is_none():
    g = ground_name("foobar nonsense", [])
    assert g["match"] == "none" and g["suggested_iri"] is None and g["n_found"] == 0


def test_preferred_ontology_breaks_ties():
    docs = [doc(["hepatocyte"], "NCIT", ident="C12588"),
            doc(["hepatocyte"], "CL", ident="CL_0000182")]
    # both exact; preference puts CL first
    g = ground_name("hepatocyte", docs, preferred=["CL", "NCIT"])
    assert g["ontology"] == "CL"
    # without preference, Solr order (NCIT first) wins
    g2 = ground_name("hepatocyte", docs)
    assert g2["ontology"] == "NCIT"


# ── normalise_iri ─────────────────────────────────────────────────────────────

def test_normalise_iri_obo_prefixed():
    assert normalise_iri("PR", "PR:000031316") \
        == "http://purl.obolibrary.org/obo/PR_000031316"


def test_normalise_iri_mesh_and_full_http():
    assert normalise_iri("MESH", "D006965") == "http://id.nlm.nih.gov/mesh/D006965"
    assert normalise_iri("GO", "http://x/GO_1") == "http://x/GO_1"   # already a URL


def test_normalise_iri_wiki_action_has_no_iri():
    assert normalise_iri("WIKI", "1") is None
    assert normalise_iri("PR", None) is None


def test_normalise_iri_generic_obo_fallback():
    assert normalise_iri("HP", "0001249") \
        == "http://purl.obolibrary.org/obo/HP_0001249"


# ── build_aopwiki_index ───────────────────────────────────────────────────────

def test_build_aopwiki_index():
    records = [
        ("object", "NADH dehydrogenase", "PR", "PR:000031316"),
        ("process", "apoptosis", "GO", "GO:0006915"),
        ("process", "Apoptosis", "GO", "GO:0006915"),   # dup by normalised name
        ("action", "increased", "WIKI", "1"),
    ]
    idx = build_aopwiki_index(records)
    assert idx["terms"]["apoptosis"]["iri"] == \
        "http://purl.obolibrary.org/obo/GO_0006915"
    assert idx["terms"]["increased"]["iri"] is None          # WIKI action → no IRI
    assert idx["ontologies"]["process"]["GO"] == 2           # both apoptosis spellings
    assert idx["n_terms"]["object"] == 1
