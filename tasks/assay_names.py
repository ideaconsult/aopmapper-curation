"""
assay_names.py — assay-name consolidation helpers shared by tasks/assay_consensus2solr.py.
Import-safe library (no Ploomber params, no side effects), so it can be unit-tested.

punct-normalise -> synonym map -> stable group key. The synonym file maps
canonical -> [variants]; both sides are punct-normalised at build time.
"""
from __future__ import annotations

import json
import os
import logging
import re
from collections import Counter, defaultdict
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


def resolve_key(key: str, synonym_map: dict[str, str]) -> str:
    """Follow the synonym map from a punct-normalised key to its final canonical
    (name -> canonical -> canonical's canonical ...). A cycle is a map error."""
    seen = [key]
    cur = key
    while True:
        nxt = synonym_map.get(cur, cur)
        if nxt == cur:
            return cur
        if nxt in seen:
            raise ValueError("synonym map cycle: " + " -> ".join(seen + [nxt]))
        seen.append(nxt)
        cur = nxt


def resolve(name: str, synonym_map: dict[str, str]) -> str:
    """punct_normalize -> synonym lookup (transitive). Returns the stable group key."""
    return resolve_key(punct_normalize(name), synonym_map)


# ── permanent sequential ids (ASSAY<n>, TECH<n>) ──────────────────────────────
# Like the wiki's KE1234 / AOP124, an id is a plain sequential number: permanent handles,
# assigned once per identity from an APPEND-ONLY registry (entries are never removed or
# modified, a number is never given to a different identity). The only thing that changes
# a live id is an explicit technique merge, and that is always reported. Each registry
# lives in a pipeline-maintained resource
# (resources/assay_ids.json, resources/technique_ids.json, written by
# tasks/assay_consensus2solr.py, same pattern as resources/assay_synonyms.json):
# {identity -> n}. The helpers below are generic; the identities differ:
#   assay      KE + punct-normalised assay name + punct-normalised measured endpoint
#              (the record itself, deliberately NOT its technique)
#   technique  the group key: punct-normalised canonical name after synonym resolution

def load_id_registry(path) -> dict[str, int]:
    """The registry; an absent file is an empty registry (first run starts at 1).
    A number must be positive and used by one identity only."""
    p = Path(path)
    if not p.exists():
        return {}
    reg = {k: int(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    if any(n < 1 for n in reg.values()):
        raise ValueError(f"{p}: ids must be positive integers")
    dup = [n for n, c in Counter(reg.values()).items() if c > 1]
    if dup:
        raise ValueError(f"{p}: number(s) assigned to more than one identity: "
                         f"{sorted(dup)[:10]}")
    return reg


def write_id_registry(registry: dict[str, int], path) -> None:
    """Atomic write (temp file in the same directory, then replace), ordered by number."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ordered = dict(sorted(registry.items(), key=lambda kv: kv[1]))
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(json.dumps(ordered, indent=1, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)       # nothing left behind if the write failed


def assign_ids(identities, registry: dict[str, int]) -> int:
    """Give every identity not yet in `registry` the next number (max + 1, in sorted
    identity order so the result does not depend on record order). Existing numbers are
    never touched or reused, even for identities that disappeared. Mutates `registry`;
    returns the number of new assignments."""
    nxt = max(registry.values(), default=0)
    new = sorted(set(identities) - registry.keys())
    for ident in new:
        nxt += 1
        registry[ident] = nxt
    return len(new)


# assays -----------------------------------------------------------------------

def assay_identity(ke_id: str, assay_name: str, measured_endpoint: str = "") -> str:
    return "|".join((ke_id, punct_normalize(assay_name or ""),
                     punct_normalize(measured_endpoint or "")))


def ke_assay_id(registry: dict[str, int], ke_id: str, assay_name: str,
                measured_endpoint: str = "") -> str:
    """Solr id of a ke_assay doc: ASSAY<n>. KeyError if the identity was not assigned."""
    return f"ASSAY{registry[assay_identity(ke_id, assay_name, measured_endpoint)]}"


# techniques -------------------------------------------------------------------

def assign_technique_ids(group_keys, registry: dict[str, int],
                         synonym_map: dict[str, str]) -> tuple[dict[str, int], dict[int, int]]:
    """(group key -> technique number, retired alias number -> surviving number).

    The registry is APPEND-ONLY: entries are only ever added, never removed or modified.
    A group key without a number gets max + 1 (also a new canonical whose aliases had
    numbers). A key that is currently merged into another (an alias) simply has no live
    technique; its number stays in the registry, so when the merge is undone it gets its
    own old number back. Numbers are never handed to a different identity.

    A technique id changes only through an explicit merge, and every such change is
    reported: when registered aliases are absorbed into a group key, the group key's own
    number survives (a new canonical gets a fresh one), and each absorbed alias number is
    returned in `retired` as {alias number: surviving number} - references to those
    numbers go stale by necessity, the caller logs and publishes the map.

    Aliases are resolved transitively (synonym_map is followed to its final canonical).
    Raises ValueError if two group keys would end up with the same number. Mutates
    `registry`."""
    gks = sorted(set(group_keys))
    assign_ids([gk for gk in gks if gk not in registry], registry)
    numbers = {gk: registry[gk] for gk in gks}
    clash = [n for n, c in Counter(numbers.values()).items() if c > 1]
    if clash:
        raise ValueError(
            "technique number(s) shared by several group keys (synonym map not "
            f"consistent with the registry): {sorted(clash)[:10]}")
    retired: dict[int, int] = {}
    for key, n in registry.items():
        root = resolve_key(key, synonym_map)
        if root != key and root in numbers:
            retired[n] = numbers[root]
    return numbers, retired


def technique_id(number: int) -> str:
    return f"TECH{number}"
