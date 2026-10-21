"""
export_assays_xml.py — Ploomber task: curated assays -> XML (+ report, + static HTML).

Reads the filtered, label-bearing records and the technique dictionary that
tasks/assay_consensus2solr.py emits as its `xml_records` product (so the XML uses exactly
the curation filters: current-release KEs, OLS-valid ontology ids, assay_min_support),
maps them onto the classes generated from the single resources/methods2aop_schema_proposal.xsd
(tasks/assay_xml.py) and writes one document for all Key Events:

  <products>/xml/assays.xml       root <assays>: <techniques>, <assay> entries (GUID ids,
                                  linked to Key Event and technique by GUID) and a <lookup>
                                  section with the stable ids (aop-wiki-id, TECH<n>, ASSAY<n>)
  <products>/xml/assays.html      the same via resources/xslt/assays.xsl (lxml), because
                                  browsers do not run XSLT processing instructions on file://
  <products>/xml/xml_report.json  counts: coerced enums, dropped ontology terms, unknown
                                  prefixes, skipped records, validation failures

The document is validated against the XSD (XSD 1.1, single pass); the report is written
first, then the task fails if validation did not pass.

Ploomber:
  upstream: ["assay_consensus2solr"]
  product:  {"nb", "xml", "html", "report"}
  params:   assays_xsd, assays_xslt
"""
from __future__ import annotations

import json
from pathlib import Path

from tasks.assay_xml import XmlReport, build_assays, render_html, validate, write_xml
from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["assay_consensus2solr"]
product = None
assays_xsd: str = "resources/methods2aop_schema_proposal.xsd"
assays_xslt: str = "resources/xslt/assays.xsl"
# -

logger = setup_logging(Path(product["report"]).parent / "export_assays_xml.log")

with open(upstream["assay_consensus2solr"]["xml_records"], encoding="utf-8") as f:
    xml_records = json.load(f)
records, techniques = xml_records["records"], xml_records["techniques"]
logger.info(f"{len(records)} records, {len(techniques)} techniques from "
            f"assay_consensus2solr")

report = XmlReport()
assays = build_assays(records, report, techniques)
xml_path = write_xml(assays, product["xml"])
logger.info(f"wrote {xml_path}: {report.n_assays} assays over {report.n_kes} KEs")

errors = validate(xml_path, assays_xsd, report)
logger.info(f"validation against {assays_xsd}: {len(errors)} errors")

html_path = render_html(xml_path, assays_xslt, product["html"])
logger.info(f"wrote {html_path}")

with open(product["report"], "w", encoding="utf-8") as f:
    json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
logger.info(f"wrote {product['report']}: {report.to_dict()}")

if errors:
    raise RuntimeError(f"{len(errors)} schema validation errors; first: {errors[0]}")
