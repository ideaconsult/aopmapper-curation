"""
wiki_diff.py — diff key events (KEs) and key event components (KECs:
object/process/action biological-event triples) between two parsed
AOP-Wiki releases (aopkb.Data instances).

Comparisons are keyed by the human wiki id (e.g. "KE123", "GO_0006915"),
not the internal UUID `id` attribute — UUIDs are AOP-Wiki-XML-export-internal
identifiers, the wiki id is the stable public one. Resolved via
tasks.utils.build_wiki_id_lookup (same technique tasks/ke_graph.py uses).

Import-safe library, no Ploomber I/O — see tasks/diff_wiki_releases.py for
the pipeline task that wires this to files.
"""
from __future__ import annotations

import re

from aopkb import Data
from tasks.utils import get_kid

KE_FIELDS = [
    "title", "short_name", "biological_organization_level", "description",
    "measurement_methodology", "evidence_supporting_taxonomic_applicability",
    "associated_tests", "references",
]

TERM_QNAMES = {
    "biological_object": "biological-object-reference",
    "biological_process": "biological-process-reference",
    "biological_action": "biological-action-reference",
}


def index_kes(data: Data, lookup: dict) -> dict[str, object]:
    """wiki-id -> KeyEvent model, skipping KEs without a resolvable wiki id."""
    out = {}
    for ke in data.key_event:
        wiki_id = get_kid(lookup, ke.id, "key-event-reference")
        if wiki_id:
            out[wiki_id] = ke
    return out


def index_terms(data: Data, lookup: dict, item: str) -> dict[str, dict]:
    """wiki-id -> term dict {name, source, source_id}, by biological_object/process/action."""
    qname = TERM_QNAMES[item]
    out = {}
    for term in getattr(data, item, []):
        wiki_id = get_kid(lookup, term.id, qname)
        if wiki_id:
            out[wiki_id] = {"name": term.name, "source": term.source, "source_id": term.source_id}
    return out


def term_to_kes(data: Data, lookup: dict) -> dict[str, dict[str, set[str]]]:
    """{"biological_object": {term_wiki_id: {ke_wiki_id, ...}}, "biological_process": ..., ...}

    Reverse index from each object/process/action wiki id to the KE wiki ids whose
    biological-event triples reference it, so a term diff can say which KE(s) it affects.
    """
    out = {item: {} for item in TERM_QNAMES}
    for ke in data.key_event:
        ke_wiki_id = get_kid(lookup, ke.id, "key-event-reference")
        if not ke_wiki_id:
            continue
        events = ke.biological_events.biological_event if ke.biological_events else []
        for ev in events:
            for item, ev_field in (("biological_object", "object_id"),
                                    ("biological_process", "process_id"),
                                    ("biological_action", "action_id")):
                term_id = getattr(ev, ev_field)
                term_wiki_id = get_kid(lookup, term_id, TERM_QNAMES[item])
                if term_wiki_id:
                    out[item].setdefault(term_wiki_id, set()).add(ke_wiki_id)
    return out


def triple_wiki_ids(ke, lookup: dict) -> set[tuple]:
    """The set of (object_wiki_id, process_wiki_id, action_wiki_id) triples on a KE."""
    events = ke.biological_events.biological_event if ke.biological_events else []
    triples = set()
    for ev in events:
        obj = get_kid(lookup, ev.object_id, TERM_QNAMES["biological_object"])
        proc = get_kid(lookup, ev.process_id, TERM_QNAMES["biological_process"])
        act = get_kid(lookup, ev.action_id, TERM_QNAMES["biological_action"])
        triples.add((obj, proc, act))
    return triples


def field_diff(old, new, fields: list[str]) -> dict:
    changed = {}
    for f in fields:
        ov, nv = getattr(old, f, None), getattr(new, f, None)
        ov = ov.value if hasattr(ov, "value") else ov
        nv = nv.value if hasattr(nv, "value") else nv
        if ov != nv:
            changed[f] = {"old": ov, "new": nv}
    return changed


def diff_terms(old_data: Data, old_lookup: dict, new_data: Data, new_lookup: dict, item: str,
                old_term_kes: dict[str, set[str]] | None = None,
                new_term_kes: dict[str, set[str]] | None = None) -> dict:
    old_idx = index_terms(old_data, old_lookup, item)
    new_idx = index_terms(new_data, new_lookup, item)
    old_term_kes = old_term_kes or {}
    new_term_kes = new_term_kes or {}
    added = sorted(new_idx.keys() - old_idx.keys())
    removed = sorted(old_idx.keys() - new_idx.keys())
    common = old_idx.keys() & new_idx.keys()
    changed = {}
    for wid in sorted(common):
        d = {f: {"old": old_idx[wid][f], "new": new_idx[wid][f]}
             for f in ("name", "source", "source_id") if old_idx[wid][f] != new_idx[wid][f]}
        if d:
            changed[wid] = {"name": new_idx[wid]["name"], "fields": d,
                             "ke_ids": sorted(new_term_kes.get(wid, old_term_kes.get(wid, set())))}
    return {
        "added": [{"wiki_id": w, "name": new_idx[w]["name"],
                    "ke_ids": sorted(new_term_kes.get(w, set()))} for w in added],
        "removed": [{"wiki_id": w, "name": old_idx[w]["name"],
                      "ke_ids": sorted(old_term_kes.get(w, set()))} for w in removed],
        "changed": changed,
        "n_old": len(old_idx), "n_new": len(new_idx),
    }


def _fmt_triple(t: tuple) -> dict:
    return {"object": t[0], "process": t[1], "action": t[2]}


def _sorted_triples(triples: set[tuple]) -> list[dict]:
    return [_fmt_triple(t) for t in sorted(triples, key=str)]


def diff_kes(old_data: Data, old_lookup: dict, new_data: Data, new_lookup: dict) -> dict:
    old_kes = index_kes(old_data, old_lookup)
    new_kes = index_kes(new_data, new_lookup)

    added_ids = sorted(new_kes.keys() - old_kes.keys())
    removed_ids = sorted(old_kes.keys() - new_kes.keys())
    common_ids = old_kes.keys() & new_kes.keys()

    # A KE that is new to the release brings all of its triples with it, and one that is
    # gone takes all of its own away; counting triples only on KEs common to both releases
    # silently omits both, which understates how much the structured layer moved.
    added = [{"wiki_id": w, "title": new_kes[w].title,
              "kec_triples_added": _sorted_triples(triple_wiki_ids(new_kes[w], new_lookup)),
              "kec_triples_removed": []} for w in added_ids]
    removed = [{"wiki_id": w, "title": old_kes[w].title,
                "kec_triples_added": [],
                "kec_triples_removed": _sorted_triples(triple_wiki_ids(old_kes[w], old_lookup))}
               for w in removed_ids]

    changed = {}
    for wiki_id in sorted(common_ids):
        old_ke, new_ke = old_kes[wiki_id], new_kes[wiki_id]
        field_changes = field_diff(old_ke, new_ke, KE_FIELDS)

        old_triples = triple_wiki_ids(old_ke, old_lookup)
        new_triples = triple_wiki_ids(new_ke, new_lookup)
        triples_added = _sorted_triples(new_triples - old_triples)
        triples_removed = _sorted_triples(old_triples - new_triples)

        if field_changes or triples_added or triples_removed:
            changed[wiki_id] = {
                "title": new_ke.title,
                "fields": field_changes,
                "kec_triples_added": triples_added,
                "kec_triples_removed": triples_removed,
            }

    n_triples_old = sum(len(triple_wiki_ids(ke, old_lookup)) for ke in old_kes.values())
    n_triples_new = sum(len(triple_wiki_ids(ke, new_lookup)) for ke in new_kes.values())
    n_triples_added = sum(len(e["kec_triples_added"]) for e in added) \
        + sum(len(c["kec_triples_added"]) for c in changed.values())
    n_triples_removed = sum(len(e["kec_triples_removed"]) for e in removed) \
        + sum(len(c["kec_triples_removed"]) for c in changed.values())

    return {
        "added": added, "removed": removed, "changed": changed,
        "n_old": len(old_kes), "n_new": len(new_kes),
        "n_triples_old": n_triples_old, "n_triples_new": n_triples_new,
        "n_triples_added": n_triples_added, "n_triples_removed": n_triples_removed,
    }


def diff_release(old_data: Data, old_lookup: dict, new_data: Data, new_lookup: dict,
                  version_old=None, version_new=None) -> dict:
    old_term_kes = term_to_kes(old_data, old_lookup)
    new_term_kes = term_to_kes(new_data, new_lookup)
    return {
        "version_old": version_old,
        "version_new": version_new,
        "key_event": diff_kes(old_data, old_lookup, new_data, new_lookup),
        "biological_object": diff_terms(old_data, old_lookup, new_data, new_lookup, "biological_object",
                                         old_term_kes["biological_object"], new_term_kes["biological_object"]),
        "biological_process": diff_terms(old_data, old_lookup, new_data, new_lookup, "biological_process",
                                          old_term_kes["biological_process"], new_term_kes["biological_process"]),
        "biological_action": diff_terms(old_data, old_lookup, new_data, new_lookup, "biological_action",
                                         old_term_kes["biological_action"], new_term_kes["biological_action"]),
    }


# ── reporting ─────────────────────────────────────────────────────────────────

def _fmt_triples(triples: list[dict]) -> str:
    return "; ".join(f"{t['object'] or '-'} / {t['process'] or '-'} / {t['action'] or '-'}" for t in triples)


def ke_report_rows(report: dict) -> list[dict]:
    """One row per added/removed/changed KE, flattened for a TSV/spreadsheet.

    status is "added", "removed", or "changed". For "changed" rows, changed_fields
    lists which of KE_FIELDS differ, and the description/measurement_methodology
    old+new text is broken into its own columns since those are the two fields
    most worth reading side by side.
    """
    ke = report["key_event"]
    rows = []
    for status in ("added", "removed"):
        for entry in ke[status]:
            rows.append({
                "status": status, "wiki_id": entry["wiki_id"], "title": entry["title"],
                "changed_fields": "", "description_old": "", "description_new": "",
                "measurement_methodology_old": "", "measurement_methodology_new": "",
                "kec_triples_added": _fmt_triples(entry["kec_triples_added"]),
                "kec_triples_removed": _fmt_triples(entry["kec_triples_removed"]),
            })
    for wiki_id, c in ke["changed"].items():
        fields = c["fields"]
        rows.append({
            "status": "changed", "wiki_id": wiki_id, "title": c["title"],
            "changed_fields": ", ".join(sorted(fields)),
            "description_old": fields.get("description", {}).get("old", ""),
            "description_new": fields.get("description", {}).get("new", ""),
            "measurement_methodology_old": fields.get("measurement_methodology", {}).get("old", ""),
            "measurement_methodology_new": fields.get("measurement_methodology", {}).get("new", ""),
            "kec_triples_added": _fmt_triples(c["kec_triples_added"]),
            "kec_triples_removed": _fmt_triples(c["kec_triples_removed"]),
        })
    return rows


KE_REPORT_COLUMNS = [
    "status", "wiki_id", "title", "changed_fields",
    "description_old", "description_new",
    "measurement_methodology_old", "measurement_methodology_new",
    "kec_triples_added", "kec_triples_removed",
]


def term_report_rows(report: dict) -> list[dict]:
    """One row per added/removed/changed biological object/process/action term,
    across all three categories, flattened for a TSV/spreadsheet.

    ke_ids lists the wiki ids of the KEs whose biological-event triples reference
    the term (from the new release for added/changed, the old release for removed).
    """
    rows = []
    for item in ("biological_object", "biological_process", "biological_action"):
        d = report[item]
        for entry in d["added"]:
            rows.append({
                "category": item, "status": "added", "wiki_id": entry["wiki_id"], "name": entry["name"],
                "changed_fields": "", "ke_ids": ", ".join(entry["ke_ids"]),
            })
        for entry in d["removed"]:
            rows.append({
                "category": item, "status": "removed", "wiki_id": entry["wiki_id"], "name": entry["name"],
                "changed_fields": "", "ke_ids": ", ".join(entry["ke_ids"]),
            })
        for wiki_id, c in d["changed"].items():
            rows.append({
                "category": item, "status": "changed", "wiki_id": wiki_id, "name": c["name"],
                "changed_fields": ", ".join(sorted(c["fields"])), "ke_ids": ", ".join(c["ke_ids"]),
            })
    return rows


TERM_REPORT_COLUMNS = ["category", "status", "wiki_id", "name", "changed_fields", "ke_ids"]


def _slugify(heading: str, seen: dict[str, int]) -> str:
    """GitHub-style heading -> anchor slug, de-duplicated against slugs already used."""
    slug = re.sub(r"[^\w\s-]", "", heading.lower()).strip()
    slug = re.sub(r"[\s]+", "-", slug)
    if slug in seen:
        seen[slug] += 1
        return f"{slug}-{seen[slug]}"
    seen[slug] = 0
    return slug


def render_markdown(report: dict) -> str:
    """Human-readable report: a table of contents (clickable anchor links) followed by
    added/removed/changed KEs (with field diffs and KEC triple diffs) and added/removed/
    changed biological object/process/action terms.
    """
    ke = report["key_event"]
    slugs: dict[str, int] = {}
    title = (
        f"# AOP-Wiki release diff: {report['version_old']} -> {report['version_new']}\n\n"
        f"Key events: {ke['n_old']} -> {ke['n_new']} "
        f"(+{len(ke['added'])} / -{len(ke['removed'])} / ~{len(ke['changed'])} changed)\n\n"
        f"KEC triples: {ke['n_triples_old']} -> {ke['n_triples_new']} "
        f"(+{ke['n_triples_added']} / -{ke['n_triples_removed']}, "
        f"counting added and removed KEs as well as changed ones)\n"
    )
    toc = ["## Contents", ""]
    lines = []

    def heading(level: int, text: str, toc_indent: int = 0) -> None:
        lines.append(f"{'#' * level} {text}")
        slug = _slugify(text, slugs)
        toc.append(f"{'  ' * toc_indent}- [{text}](#{slug})")

    def _with_triples(entry: dict, key: str, label: str) -> str:
        triples = entry[key]
        return f" — {label}: {_fmt_triples(triples)}" if triples else " — no KEC triples"

    if ke["added"]:
        heading(2, "KEs added")
        for e in ke["added"]:
            lines.append(f"- **{e['wiki_id']}** — {e['title']}"
                         f"{_with_triples(e, 'kec_triples_added', 'KEC triples added')}")
        lines.append("")

    if ke["removed"]:
        heading(2, "KEs removed")
        for e in ke["removed"]:
            lines.append(f"- **{e['wiki_id']}** — {e['title']}"
                         f"{_with_triples(e, 'kec_triples_removed', 'KEC triples removed')}")
        lines.append("")

    if ke["changed"]:
        heading(2, "KEs changed")
        for wiki_id, c in ke["changed"].items():
            heading(3, f"{wiki_id} — {c['title']}", toc_indent=1)
            for field, fv in c["fields"].items():
                lines.append(f"- **{field}** changed:")
                lines.append(f"  - old: {fv['old']!r}")
                lines.append(f"  - new: {fv['new']!r}")
            if c["kec_triples_added"]:
                lines.append(f"- KEC triples added: {_fmt_triples(c['kec_triples_added'])}")
            if c["kec_triples_removed"]:
                lines.append(f"- KEC triples removed: {_fmt_triples(c['kec_triples_removed'])}")
            lines.append("")

    def _ke_ids_str(ke_ids: list[str]) -> str:
        return f" (KEs: {', '.join(ke_ids)})" if ke_ids else " (no KE references it)"

    for item in ("biological_object", "biological_process", "biological_action"):
        d = report[item]
        if not (d["added"] or d["removed"] or d["changed"]):
            continue
        heading(2, f"{item.replace('_', ' ').title()}: "
                   f"{d['n_old']} -> {d['n_new']} "
                   f"(+{len(d['added'])} / -{len(d['removed'])} / ~{len(d['changed'])} changed)")
        for e in d["added"]:
            lines.append(f"- added **{e['wiki_id']}** — {e['name']}{_ke_ids_str(e['ke_ids'])}")
        for e in d["removed"]:
            lines.append(f"- removed **{e['wiki_id']}** — {e['name']}{_ke_ids_str(e['ke_ids'])}")
        for wiki_id, c in d["changed"].items():
            field_str = "; ".join(f"{f}: {fv['old']!r} -> {fv['new']!r}" for f, fv in c["fields"].items())
            lines.append(f"- changed **{wiki_id}** — {c['name']}: {field_str}{_ke_ids_str(c['ke_ids'])}")
        lines.append("")

    return title + "\n" + "\n".join(toc) + "\n\n" + "\n".join(lines)
