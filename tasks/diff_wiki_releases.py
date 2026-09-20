"""
diff_wiki_releases.py — Ploomber task: diff key events and key event components
(KECs: object/process/action biological-event triples) between two AOP-Wiki XML
releases, each already parsed to aop.json by parse_pydantic (one parse_pydantic
task per version, see pipeline.wikidiff.yaml). Diff logic lives in tasks/wiki_diff.py.

Ploomber:
  upstream: ["parse_pydantic_<version_old>", "parse_pydantic_<version_new>"]
  product:  {"json": "...full diff...", "csv": "...per-category summary...",
             "ke_tsv": "...one row per added/removed/changed KE...",
             "term_tsv": "...one row per added/removed/changed biological object/process/action...",
             "md": "...human-readable report..."}
  params:   version_old, version_new
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from aopkb import Data
from tasks.logutil import setup_logging
from tasks.utils import preprocess_timestamps, build_wiki_id_lookup
from tasks.wiki_diff import (
    diff_release, ke_report_rows, KE_REPORT_COLUMNS,
    term_report_rows, TERM_REPORT_COLUMNS, render_markdown,
)

# + tags=["parameters"]
upstream = None
product = None
version_old = None
version_new = None
# -

OUT_JSON = Path(product["json"])
OUT_CSV = Path(product["csv"])
OUT_KE_TSV = Path(product["ke_tsv"])
OUT_TERM_TSV = Path(product["term_tsv"])
OUT_MD = Path(product["md"])

logger = setup_logging(OUT_JSON.parent / "diff_wiki_releases.log")


def load_release(json_path: str) -> tuple[Data, dict]:
    with open(json_path, encoding="utf-8") as f:
        json_data = json.load(f)
    preprocess_timestamps(json_data)
    data = Data.model_validate(json_data)
    return data, build_wiki_id_lookup(data)


old_data, old_lookup = load_release(upstream[f"parse_pydantic_{version_old}"]["json"])
new_data, new_lookup = load_release(upstream[f"parse_pydantic_{version_new}"]["json"])

report = diff_release(old_data, old_lookup, new_data, new_lookup, version_old, version_new)

OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["category", "n_old", "n_new", "n_added", "n_removed", "n_changed"])
    for cat in ("key_event", "biological_object", "biological_process", "biological_action"):
        d = report[cat]
        w.writerow([cat, d["n_old"], d["n_new"], len(d["added"]), len(d["removed"]), len(d["changed"])])
    # KEC triples are attached to KEs rather than being a top-level category, so they get
    # their own summary row: this is the number that says how much the structured layer moved.
    _ke = report["key_event"]
    w.writerow(["kec_triple", _ke["n_triples_old"], _ke["n_triples_new"],
                _ke["n_triples_added"], _ke["n_triples_removed"], ""])

with open(OUT_KE_TSV, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=KE_REPORT_COLUMNS, delimiter="\t")
    w.writeheader()
    w.writerows(ke_report_rows(report))

with open(OUT_TERM_TSV, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=TERM_REPORT_COLUMNS, delimiter="\t")
    w.writeheader()
    w.writerows(term_report_rows(report))

with open(OUT_MD, "w", encoding="utf-8") as f:
    f.write(render_markdown(report))

ke = report["key_event"]
n_field_changed = sum(1 for c in ke["changed"].values() if c["fields"])
n_text_only = sum(1 for c in ke["changed"].values()
                  if c["fields"] and not (c["kec_triples_added"] or c["kec_triples_removed"]))
logger.info(
    f"KE {version_old} -> {version_new}: "
    f"{ke['n_old']} -> {ke['n_new']} "
    f"(+{len(ke['added'])} / -{len(ke['removed'])} / ~{len(ke['changed'])} changed, "
    f"{n_field_changed} with field edits, {n_text_only} with field edits only; "
    f"KEC triples {ke['n_triples_old']} -> {ke['n_triples_new']} "
    f"(+{ke['n_triples_added']} / -{ke['n_triples_removed']})"
)
