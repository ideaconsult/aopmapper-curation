"""
manuscript_publish.py — copy raw pipeline products into the manuscript repo's
generated/ folder unchanged. Import-safe library used by tasks/export_manuscript_results.py.
"""
from __future__ import annotations

import shutil
from pathlib import Path

TECHNIQUE_STATS_NAME = "assay_technique_stats.json"


def publish_technique_stats(technique_stats_json: str | None,
                            manuscript_repo: str | Path) -> Path | None:
    """Copy the assay stage's assay_technique_stats.json (verbatim, so the manuscript can
    cite every field, not just the subset _technique_stats summarises) to
    <manuscript_repo>/manuscript/generated/assay_technique_stats.json.
    Returns the destination, or None when no usable source was given."""
    if not technique_stats_json or not Path(technique_stats_json).exists():
        return None
    dest = Path(manuscript_repo) / "manuscript" / "generated" / TECHNIQUE_STATS_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(technique_stats_json, dest)
    return dest
