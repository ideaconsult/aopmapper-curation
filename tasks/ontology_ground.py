"""
ontology_ground.py — ground a biological term name to an ontology IRI via the
IdeaConsult dictionary search (~30 ontologies, open API).  Import-safe library.

The dictionary endpoint is a Solr index of ontology terms. A GET like
  /dictionary?q=name:"hepatocyte"
returns ranked docs, each with: `id`, `prefix` (base URI), `ontology`, and a `name`
synonym list. The full IRI is `prefix + "/" + id` (verified against the `Parents` IRIs).

Used for the object/process slots of a KEC triple — which are open-vocabulary named
entities the LLM often leaves un-grounded (iri=null). The tagger API is better for long
text; for a single short entity name, dictionary search is the right tool.
"""
from __future__ import annotations

import logging
from typing import Optional

from tasks.kec_compare import _norm_name

logger = logging.getLogger(__name__)

DEFAULT_DICT_URL = "https://api.ideaconsult.net/nanoreg1/dictionary"

_OBO = "http://purl.obolibrary.org/obo/"
_IRI_PREFIX = {
    "GO": _OBO + "GO_", "CL": _OBO + "CL_", "CHEBI": _OBO + "CHEBI_",
    "UBERON": _OBO + "UBERON_", "PR": _OBO + "PR_", "MP": _OBO + "MP_",
    "HP": _OBO + "HP_", "PCO": _OBO + "PCO_", "NBO": _OBO + "NBO_",
    "MI": _OBO + "MI_", "VT": _OBO + "VT_", "PATO": _OBO + "PATO_",
    "IDO": _OBO + "IDO_", "RBO": _OBO + "RBO_",
    "FMA": "http://purl.org/sig/ont/fma/fma",
    "MESH": "http://id.nlm.nih.gov/mesh/",
}


def normalise_iri(source: Optional[str], source_id: Optional[str]) -> Optional[str]:
    """Build a full ontology IRI from AOP-Wiki's (source, source_id), e.g.
    ('PR', 'PR:000031316') → http://purl.obolibrary.org/obo/PR_000031316.
    Returns None for the WIKI controlled-vocabulary actions (numeric ids, no IRI)."""
    if not source_id:
        return None
    sid = str(source_id).strip()
    if sid.startswith("http"):
        return sid
    src = (source or "").upper()
    for p in (f"{src}:", f"{source}:", f"{src.lower()}:"):
        if sid.startswith(p):
            sid = sid[len(p):]
            break
    if src in ("WIKI", "N/A", "NONE", "", "TAIR"):
        return None
    base = _IRI_PREFIX.get(src)
    if base:
        return f"{base}{sid}"
    return f"{_OBO}{src}_{sid}"          # generic OBO fallback


def build_aopwiki_index(records) -> dict:
    """
    Build an index of the ontology terms AOP-Wiki already uses, from
    `records` = iterable of (slot, name, source, source_id).

    Returns {"terms": {norm_name: {name, source, source_id, iri, slot}},
             "ontologies": {slot: {source: count}},
             "n_terms": {slot: count}}.
    """
    terms, onto = {}, {}
    for slot, name, source, sid in records:
        if not name:
            continue
        onto.setdefault(slot, {})
        onto[slot][source or "NONE"] = onto[slot].get(source or "NONE", 0) + 1
        key = _norm_name(name)
        terms.setdefault(key, {"name": name, "source": source, "source_id": sid,
                               "iri": normalise_iri(source, sid), "slot": slot})
    return {"terms": terms, "ontologies": onto,
            "n_terms": {s: sum(c.values()) for s, c in onto.items()}}


def _iri(doc: dict) -> Optional[str]:
    prefix = (doc.get("prefix") or [None])[0]
    ident = doc.get("id")
    return f"{prefix}/{ident}" if prefix and ident else None


def dict_lookup(name: str, *, url: str = DEFAULT_DICT_URL, rows: int = 10,
                timeout: float = 20.0, client=None) -> list[dict]:
    """Return the dictionary docs matching `name` (Solr phrase query, relevance-ranked)."""
    import httpx
    safe = str(name).replace("\\", "\\\\").replace('"', '\\"')
    q = f'name:"{safe}"'
    own = client is None
    if own:
        client = httpx.Client(timeout=timeout)
    try:
        r = client.get(url, params={"q": q, "rows": rows})
        r.raise_for_status()
        return r.json().get("response", {}).get("docs", [])
    finally:
        if own:
            client.close()


def ground_name(name: str, docs: list[dict], *, preferred: list = None) -> dict:
    """
    Pick the best ontology grounding for `name` from dictionary `docs`.

    Returns {match, suggested_iri, ontology, suggested_name, n_found} where
    match ∈ {"exact", "fuzzy", "none"}:
      exact — a returned synonym normalises equal to the query name (high confidence);
      fuzzy — only partial/related matches (a suggestion to review);
      none  — the name matches no ontology term (a red flag — possible hallucination).
    `preferred` optionally reorders ties by ontology code (else Solr relevance order).
    """
    if not docs:
        return {"match": "none", "suggested_iri": None, "ontology": None,
                "suggested_name": None, "n_found": 0}
    nq = _norm_name(name)
    exact = [d for d in docs
             if any(_norm_name(n) == nq for n in (d.get("name") or []))]
    pool = exact if exact else docs
    if preferred:
        def rank(d):
            o = (d.get("ontology") or [""])[0]
            return preferred.index(o) if o in preferred else len(preferred)
        pool = sorted(pool, key=rank)
    best = pool[0]
    names = best.get("name") or [None]
    # for an exact hit, report the synonym that actually matched, not the primary label
    matched_name = next((n for n in names if _norm_name(n) == nq), names[0]) if exact \
        else names[0]
    return {
        "match": "exact" if exact else "fuzzy",
        "suggested_iri": _iri(best),
        "ontology": (best.get("ontology") or [None])[0],
        "suggested_name": matched_name,
        "n_found": len(docs),
    }


def ground(name: str, *, url: str = DEFAULT_DICT_URL, rows: int = 10,
           timeout: float = 20.0, preferred: list = None, client=None) -> dict:
    """Convenience: dict_lookup + ground_name in one call."""
    docs = dict_lookup(name, url=url, rows=rows, timeout=timeout, client=client)
    return ground_name(name, docs, preferred=preferred)
