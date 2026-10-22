"""
export_kec_xml.py — Ploomber task: cross-model KEC consensus -> XML (+ report, + static HTML).

Reads viz_consensus.json (tasks/viz_consensus.py: per Key Event the curated triples and the
clusters of model triples with their members and curated matches), derives the status of
every cluster and curated triple (confirmed / missed / novel), and writes one document of a
separate type (resources/kec_consensus_schema.xsd, classes aopkb/kec_consensus.py, library
tasks/kec_xml.py). The AOP-Wiki XML and aop.xsd are not touched; Key Events are referenced by
their AOP-Wiki GUID, taken from the `ke_id` and `uuid` of the per-model kec.json records of
the panel (`products`, models as in viz_consensus: crossmodel_compare's models_present, else
`model_keys`), i.e. from the AOP-Wiki release the KEC was extracted from. The task fails when
more than `max_unresolved_fraction` of the Key Events have no GUID.

  <dir>/xml/kec_consensus.xml   root <kec-consensus>: <summary> (panel, histogram), <terms>,
                                <key-event-result>, <consensus-triple> (members with their own
                                triples), <lookup> (KE GUID -> AOP-Wiki number)
  <dir>/xml/kec_consensus.html  the same via resources/xslt/kec_consensus.xsl (lxml), because
                                browsers do not run XSLT processing instructions on file://
  <dir>/xml/xml_report.json     counts, Key Events without a GUID, action-less clusters,
                                validation failures

Full corpus (KEC_ALL): extraction, not selection, so the viz_consensus it reads must not be
restricted to an ensemble (`viz_restrict_to_ensemble: false`); the report flags it if it was.
`min_support_ensemble` is the acceptance threshold: only clusters with at least that many
models are exported and can confirm a curated triple. This differs from the viz_consensus
page, whose JS lets a cluster of any support confirm a curated triple (the threshold there
only colours the bars): a triple matched only by clusters below the threshold is "confirmed"
on the page and "missed" here. `min_support_ensemble: 1` reproduces the page exactly.

The document is validated against the XSD (XSD 1.1) and cross-checked with lxml; the report
is written first, then the task fails if validation did not pass or too many Key Events have
no GUID.

Ploomber:
  upstream: ["crossmodel_compare", "viz_consensus"]
  product:  {"nb", "xml", "html", "report"}
  params:   products, model_keys, min_support_ensemble, max_unresolved_fraction, kec_xsd, kec_xslt
"""
from __future__ import annotations

import json
from pathlib import Path

from tasks.kec_xml import (MAX_UNRESOLVED_FRACTION, KecXmlReport, build_consensus,
                           check_unresolved, crosscheck, ke_uuids_from_kec, render_html,
                           validate, write_xml)
from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["crossmodel_compare", "viz_consensus"]
product = None
products = "products_202606"
model_keys = []
min_support_ensemble = 2
# fail when more than this share of the Key Events has no AOP-Wiki GUID in the kec.json files
max_unresolved_fraction: float = MAX_UNRESOLVED_FRACTION
kec_xsd: str = "resources/kec_consensus_schema.xsd"
kec_xslt: str = "resources/xslt/kec_consensus.xsl"
# -

logger = setup_logging(Path(product["report"]).parent / "export_kec_xml.log")

with open(upstream["viz_consensus"]["data"], encoding="utf-8") as f:
    viz = json.load(f)
with open(upstream["crossmodel_compare"]["summary"], encoding="utf-8") as f:
    summary_data = json.load(f)

# KEnnn -> GUID from the kec.json of the panel (same models and paths as viz_consensus)
present = summary_data.get("summary", {}).get("models_present") or model_keys
kecs = []
for mk in present:
    kec_path = Path(products) / mk / "kec.json"
    if not kec_path.exists():
        logger.warning(f"no kec.json for {mk} at {kec_path}")
        continue
    try:
        kecs.append(json.loads(kec_path.read_text(encoding="utf-8")))
    except Exception as ex:
        logger.warning(f"could not load kec.json for {mk}: {ex}")
ke_uuids = ke_uuids_from_kec(kecs)
logger.info(f"{len(viz['kes'])} Key Events from viz_consensus, {len(ke_uuids)} with an "
            f"AOP-Wiki GUID in the kec.json of {len(kecs)}/{len(present)} models under {products}")

report = KecXmlReport()
consensus = build_consensus(viz, ke_uuids, int(min_support_ensemble), report)
if report.restricted:
    logger.warning("viz_consensus was restricted to an ensemble subset - not the full "
                   "panel; rerun it with viz_restrict_to_ensemble: false for KEC_ALL")
xml_path = write_xml(consensus, product["xml"])
logger.info(f"wrote {xml_path}: {report.n_clusters} clusters over {report.n_kes} KEs")

errors = validate(xml_path, kec_xsd, report)
logger.info(f"validation against {kec_xsd}: {len(errors)} errors")
problems = crosscheck(xml_path, report)
logger.info(f"lxml cross-check: {len(problems)} problems")

html_path = render_html(xml_path, kec_xslt, product["html"])
logger.info(f"wrote {html_path}")

with open(product["report"], "w", encoding="utf-8") as f:
    json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
logger.info(f"wrote {product['report']}: {report.to_dict()}")

if errors or problems:
    raise RuntimeError(f"{len(errors)} schema validation errors, {len(problems)} cross-check "
                       f"problems; first: {(errors + problems)[0]}")
check_unresolved(report, float(max_unresolved_fraction))
