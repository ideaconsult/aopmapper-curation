"""
aop_select.py — shared KE-selection helpers over a parsed aopkb.Data. LIBRARY.

Import-safe. Both extract_kec.py and extract_assays.py filter KEs by the same `ke_filter`
({ke_ids, oecd_status, limit}); this module holds the OECD→UUID and UUID→wiki-id mappings
so the filtering logic is identical across extractors.
"""
from __future__ import annotations


def collect_ke_uuids_for_oecd(data, status_strings) -> set:
    """KE UUIDs belonging to ≥1 AOP whose oecd_status matches any of status_strings
    (case-insensitive substring). Accepts a string or list. E.g. "endorsed" matches
    "WPHA/WNT Endorsed"."""
    if isinstance(status_strings, str):
        status_strings = [status_strings]
    wanted = [s.strip().lower() for s in status_strings]
    uuids = set()
    for aop in data.aop:
        aop_status = aop.status.oecd_status if aop.status else None
        if aop_status is None:
            continue
        val = (aop_status.value if hasattr(aop_status, "value")
               else str(aop_status)).lower()
        if any(w in val for w in wanted) and aop.key_events:
            for ke_ref in aop.key_events.key_event:
                uuids.add(ke_ref.key_event_id)
    return uuids


def wiki_id_map_from_elements(any_element) -> dict:
    """UUID → 'KEnnn' from a list of vendor_specific `any_element` dicts (the shape is
    the same whether they come from a pydantic Data model or raw parsed JSON)."""
    mapping = {}
    for entry in any_element or []:
        if "key-event-reference" not in entry.get("qname", ""):
            continue
        attrs = entry.get("attributes", {})
        uuid = attrs.get("id")
        wiki_num = attrs.get("aop-wiki-id")
        if uuid and wiki_num:
            mapping[uuid] = f"KE{wiki_num}"
    return mapping


def build_wiki_id_map(data) -> dict:
    """UUID → 'KEnnn' from a parsed aopkb.Data (vendor_specific key-event-references)."""
    return wiki_id_map_from_elements(data.vendor_specific.any_element)


def load_wiki_id_map(aop_json_path) -> dict:
    """UUID → 'KEnnn' read straight from a parse_pydantic `aop.json` product, without
    building the pydantic Data model. Reusable by consensus/viz stages that only hold
    the JSON file (e.g. to relabel UUID-keyed outputs from older extraction runs)."""
    import json
    from pathlib import Path
    raw = json.loads(Path(aop_json_path).read_text(encoding="utf-8"))
    vs = raw.get("vendor_specific") or raw.get("vendor-specific") or {}
    return wiki_id_map_from_elements(vs.get("any_element") or vs.get("any-element"))


def ke_filter_uuids(data, ke_filter: dict | None):
    """Resolve a ke_filter ({ke_ids, oecd_status, limit}) to a predicate + limit.

    Returns (allowed_uuids_or_None, ke_ids_set_or_None, limit_or_None). A KE passes if
    (allowed_uuids is None or uuid in allowed_uuids) AND (ke_ids_set is None or uuid/
    wiki_id in ke_ids_set). `limit` falsy ⇒ no limit.
    """
    f = ke_filter or {}
    oecd = f.get("oecd_status")
    ke_ids = f.get("ke_ids")
    limit = f.get("limit") or None
    allowed = collect_ke_uuids_for_oecd(data, oecd) if oecd else None
    ke_ids_set = set(ke_ids) if ke_ids else None
    return allowed, ke_ids_set, limit
