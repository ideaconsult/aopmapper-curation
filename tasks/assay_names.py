"""
assay_names.py — assay-name consolidation helpers shared by tasks/assay_consensus2solr.py.
Import-safe library (no Ploomber params, no side effects), so it can be unit-tested.

punct-normalise -> synonym map -> stable group key. The synonym file maps
canonical -> [variants]; both sides are punct-normalised at build time.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def punct_normalize(name: str) -> str:
    """Lowercase, strip brackets/punctuation, collapse whitespace. The only step that
    touches punctuation; its output keys the synonym map."""
    n = name.lower().strip()
    n = re.sub(r"[\[\]\(\)\-_/\\]", " ", n)
    n = re.sub(r"\s+", " ", n)
    return n.strip()


def build_synonym_map(synonym_file: str) -> dict[str, str]:
    """punct_normalized(variant) -> punct_normalized(canonical). Both sides normalised
    at build time so the lookup works however the raw name arrives; each canonical also
    maps to itself so already-canonical names resolve cleanly.

    A missing file is a hard error: silently skipping it leaves the technique count
    inflated by unmerged spelling variants (the failure this guards against)."""
    path = Path(synonym_file)
    if not path.exists():
        raise FileNotFoundError(
            f"assay synonym file not found: {synonym_file} "
            f"(refusing to run without name consolidation)")
    with open(path, encoding="utf-8") as f:
        raw: dict[str, list[str]] = json.load(f)

    synonym_map: dict[str, str] = {}
    for canonical, variants in raw.items():
        norm_canonical = punct_normalize(canonical)
        synonym_map[norm_canonical] = norm_canonical
        for variant in variants:
            synonym_map[punct_normalize(variant)] = norm_canonical
    logger.info(f"synonym map: {len(raw)} groups -> {len(synonym_map)} entries")
    return synonym_map


def resolve(name: str, synonym_map: dict[str, str]) -> str:
    """punct_normalize -> synonym lookup. Returns the stable group key."""
    pn = punct_normalize(name)
    return synonym_map.get(pn, pn)
