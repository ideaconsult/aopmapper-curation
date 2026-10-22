"""
viz_assay_slim.py — shrink the per-assay payload of viz_assay_consensus.json. LIBRARY.

The data sidecar carries, for every cluster, the representative assay plus one full
AssayRecord per member model. Most of its size (about 60 of 98 MB compact) is each
record's `logprobs` block: per-field mean/min log-probabilities and, for the categorical
fields, the top-k alternatives with their token byte arrays. Nothing that reads the file
uses them: the HTML report shows only the endpoint-direction `margin`, and the export /
Solr / synonym / reference-check tasks read the plain assay fields, never `logprobs`.

Opt-in (task param `slim_data`, env key `viz_slim_data`; default off). `slim_assay` replaces `logprobs` with just
`{"endpoint_direction": {"margin": m}}` (omitted when there is no numeric margin) and
leaves every other field untouched. The full per-field log-probabilities remain in the
per-model assays.json / assays_logprobs.jsonl products, which is where calibration reads
them.

Kept by name because something reads or displays it: assay_name, description,
method_type, measured_endpoint, endpoint_direction, ontology_terms (name/iri/source),
cell_types, organ_tissue, species, guideline, throughput, evidence, kec_triple,
source_field, plus the endpoint_direction margin above.
"""
from __future__ import annotations


def _margin(assay: dict):
    """The endpoint-direction margin the report prints (None if absent/non-numeric)."""
    m = ((assay.get("logprobs") or {}).get("endpoint_direction") or {}).get("margin")
    return m if isinstance(m, (int, float)) and not isinstance(m, bool) else None


def slim_assay(assay: dict | None) -> dict:
    """Copy of `assay` with `logprobs` reduced to the endpoint-direction margin."""
    assay = assay or {}
    out = {k: v for k, v in assay.items() if k != "logprobs"}
    m = _margin(assay)
    if m is not None:
        out["logprobs"] = {"endpoint_direction": {"margin": m}}
    return out


def slim_cluster(cluster: dict) -> dict:
    """Cluster dict (support / models / assay / members) with every assay slimmed."""
    out = dict(cluster)
    out["assay"] = slim_assay(cluster.get("assay"))
    out["members"] = [{**m, "assay": slim_assay(m.get("assay"))}
                      for m in cluster.get("members") or []]
    return out
