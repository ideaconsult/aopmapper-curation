"""The slim viz_assay_consensus.json must keep every field the report shows or a downstream
task reads, and drop only the bulky logprobs block."""
import json

from scripts.compare_viz_assay_json import compare
from tasks.viz_assay_slim import slim_assay, slim_cluster


def _assay(name="HPLC-EC", margin=13.375):
    lp = {
        "assay_name": {"logprob_mean": -0.1, "logprob_min": -0.4},
        "endpoint_direction": {
            "logprob_mean": -3e-5, "logprob_min": -8e-5,
            "top_logprobs": [{"token": "not", "logprob": -4e-6, "bytes": [110, 111, 116]}],
        },
        "throughput": {"top_logprobs": [{"token": "medium", "logprob": -0.28,
                                         "bytes": [109, 101]}], "margin": 1.125},
    }
    if margin is not None:
        lp["endpoint_direction"]["margin"] = margin
    return {
        "assay_name": name,
        "description": "HPLC coupled to electrochemical detection",
        "method_type": "in_vitro",
        "cell_types": ["neuron"], "organ_tissue": ["brain"], "species": ["rat"],
        "measured_endpoint": "acetylcholine levels",
        "endpoint_direction": "not_specified",
        "ontology_terms": [{"name": "HPLC", "iri": "http://purl.obolibrary.org/obo/OBI_0000275",
                            "source": "OBI"}, {"name": "x", "iri": None, "source": None}],
        "guideline": "OECD TG 1",
        "throughput": "medium",
        "evidence": ["verbatim span one", "span two"],
        "kec_triple": {"biological_object": {"name": "acetylcholine",
                                             "iri": "http://purl.obolibrary.org/obo/CHEBI_15718",
                                             "source": "CHEBI"},
                       "biological_process": {"name": "hydrolysis", "iri": None, "source": None},
                       "biological_action": {"name": "decreased", "iri": None, "source": None}},
        "logprobs": lp,
        "source_field": "measurement_methodology",
    }


def _viz(slim):
    f = slim_cluster if slim else (lambda c: c)
    cluster = {"support": 2, "models": ["a--m1", "b--m2"], "assay": _assay(),
               "members": [{"model": "a--m1", "assay_idx": 0, "assay": _assay()},
                           {"model": "b--m2", "assay_idx": 3, "assay": _assay("HPLC", None)}]}
    return {"products": "p", "summary": {"n_models": 2, "models": ["a--m1", "b--m2"]},
            "kes": [{"ke_id": "KE10", "clusters": [f(cluster)]}]}


def test_every_displayed_or_read_field_survives():
    full, slim = _assay(), slim_assay(_assay())
    for k in ("assay_name", "description", "method_type", "cell_types", "organ_tissue",
              "species", "measured_endpoint", "endpoint_direction", "ontology_terms",
              "guideline", "throughput", "evidence", "kec_triple", "source_field"):
        assert slim[k] == full[k], k
    assert slim["logprobs"] == {"endpoint_direction": {"margin": 13.375}}


def test_no_margin_means_no_logprobs_key():
    assert "logprobs" not in slim_assay(_assay(margin=None))
    assert slim_assay(None) == {}


def test_input_not_mutated():
    a = _assay()
    slim_assay(a)
    assert a == _assay()


def test_cluster_structure_kept():
    c = _viz(False)["kes"][0]["clusters"][0]
    s = slim_cluster(c)
    assert s["support"] == 2 and s["models"] == c["models"]
    assert [m["model"] for m in s["members"]] == ["a--m1", "b--m2"]
    assert [m["assay_idx"] for m in s["members"]] == [0, 3]
    assert s["assay"]["evidence"] == c["assay"]["evidence"]


def test_slim_is_smaller_and_compare_finds_no_difference():
    full, slim = _viz(False), _viz(True)
    assert len(json.dumps(slim, separators=(",", ":"))) < len(json.dumps(full)) * 0.75
    problems, n = compare(full, slim)
    assert problems == [] and n > 20


def test_compare_detects_a_changed_displayed_value():
    full, slim = _viz(False), _viz(True)
    slim["kes"][0]["clusters"][0]["members"][1]["assay"]["evidence"] = ["changed"]
    problems, _ = compare(full, slim)
    assert any("evidence" in p for p in problems)
    slim = _viz(True)
    slim["kes"][0]["clusters"][0]["assay"]["logprobs"]["endpoint_direction"]["margin"] = 1.0
    assert any("margin" in p for p in compare(full, slim)[0])


# ---- opt-in wiring: task default off, env key -> pipeline param -> task param --------
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_task_default_is_full_records():
    src = (ROOT / "tasks" / "viz_assay_consensus.py").read_text(encoding="utf-8")
    assert re.search(r"^slim_data = False\s*$", src, re.M)


def test_env_key_wired_through_pipeline():
    env = yaml.safe_load((ROOT / "env.assays_consensus.yaml").read_text(encoding="utf-8"))
    assert env["viz_slim_data"] is True
    pipe = yaml.safe_load(
        (ROOT / "pipeline.assays_consensus.yaml").read_text(encoding="utf-8"))
    task = next(t for t in pipe["tasks"]
                if t["source"] == "tasks/viz_assay_consensus.py")
    assert task["params"]["slim_data"] == "{{viz_slim_data}}"
