"""
kec_compare.py — hybrid (object, process, action) triple matcher.  LIBRARY, not a task.

The shared primitive for comparing extracted KEC triples against the curated XML
ground truth and (later) across models. `diagnose_triple_matching.py` showed exact
ontology-ID matching yields ~zero hits, so matching is semantic.

This module is import-safe (no module-level side effects).
The Ploomber task `tasks/compare_kec.py` drives it; `kec_logprob_calibration.py` reuses it.

Tiers (per slot, cheapest first):
  1. IRI exact   — normalise object/process IRIs via iri_to_solr_id (below); equal ⇒ match.
  2. Name exact  — normalised string equality (lowercase, collapse ws/punct). No deps.
  3. Embedding   — cosine(name_a, name_b) ≥ tau, biomedical embedder (SapBERT default).
  4. Judge       — STUB this increment (see _judge_slot); wired in the label-audit increment.

The ACTION slot is a controlled vocabulary — matched by name only, never embedded.
A triple matches iff: action matches AND ≥1 of {object, process} matches.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
DEFAULT_TAU = 0.75

_NULL_STRINGS = {"null", "none", "n/a", "na", ""}


def iri_to_solr_id(iri: Optional[str]) -> Optional[str]:
    """
    Normalise a full ontology IRI to its short accession id (e.g. "UBERON_0009853").
    Returns None if absent, null-like, a bare integer (action IRI), or unrecognised.
    """
    if iri is None:
        return None
    iri = str(iri).strip()
    if iri.lower() in _NULL_STRINGS:
        return None
    # Bare integers are action IRIs — not valid for obj/proc slots
    if re.match(r"^\d+$", iri):
        return None

    # OBO Foundry
    m = re.match(r"https?://purl\.obolibrary\.org/obo/([A-Za-z]+)_(\S+)", iri)
    if m:
        return f"{m.group(1).upper()}_{m.group(2)}"

    # MeSH
    mesh = "http://id.nlm.nih.gov/mesh/"
    if iri.startswith(mesh):
        return iri[len(mesh):]

    # FMA
    fma1 = "http://purl.org/sig/ont/fma/fma"
    fma2 = "http://purl.org/sig/ont/fma/FMA:"
    if iri.startswith(fma1):
        return f"FMA_{iri[len(fma1):]}"
    if iri.startswith(fma2):
        return f"FMA_{iri[len(fma2):]}"

    # Fallback: last path/fragment component
    fragment = iri.split("/")[-1].split("#")[-1]
    return fragment if fragment else None

# Controlled action vocabulary — keep in sync with extract_kec.ALLOWED_ACTIONS.
# (Defined locally because extract_kec.py is a Ploomber task and is not import-safe.)
ALLOWED_ACTIONS = {
    "increased", "decreased", "occurrence", "disrupted", "abnormal",
    "arrested", "delayed", "premature", "pathological",
    "functional change", "morphological change",
}

_SLOTS = ("object", "process", "action")


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalise_triple(t: dict) -> dict:
    """
    Unify the two triple encodings into {object, process, action} where each
    value is a {name, iri, source} dict or None.

      structured (XML truth):  {"object": {...}, "process": {...}, "action": {...}}  (slots may be absent)
      llm_extracted.triples:   {"biological_object": {...}, "biological_process": {...}, "biological_action": {...}}
    """
    out = {}
    for slot in _SLOTS:
        term = t.get(slot)
        if term is None:
            term = t.get(f"biological_{slot}")
        out[slot] = term if (term and term.get("name")) else None
    return out


def _norm_name(name: Optional[str]) -> str:
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"[\s_\-/]+", " ", s)
    s = re.sub(r"[^\w ]+", "", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Embedder (lazy — only loaded if the embedding tier is actually used)
# ---------------------------------------------------------------------------

class Embedder:
    """Cosine-similarity name comparator backed by a sentence-transformers model.
    Encodings are cached per process (names repeat heavily across KEs)."""

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        import torch
        from sentence_transformers import SentenceTransformer  # lazy: needs torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"Loading embedder: {model_name} (device={device})")
        self.model = SentenceTransformer(model_name, device=device)
        self._cache: dict = {}

    def _vec(self, name: str):
        v = self._cache.get(name)
        if v is None:
            v = self.model.encode(name, normalize_embeddings=True,
                                   show_progress_bar=False)
            self._cache[name] = v
        return v

    def cosine(self, a: str, b: str) -> float:
        import numpy as np
        return float(np.dot(self._vec(a), self._vec(b)))   # vectors are L2-normalised


# ---------------------------------------------------------------------------
# Slot + triple matching
# ---------------------------------------------------------------------------

def _iri_match(a: dict, b: dict) -> bool:
    ida, idb = iri_to_solr_id(a.get("iri")), iri_to_solr_id(b.get("iri"))
    return bool(ida and idb and ida == idb)


def match_slot(a: Optional[dict], b: Optional[dict], *, is_action: bool,
               embedder: Optional[Embedder], tau: float,
               judge=None) -> tuple[bool, float, str]:
    """Return (matched, similarity, method) for one slot.

    `judge`, if given, is a callable judge(name_a, name_b) -> Optional[bool] used to
    resolve borderline embedding cases (cosine in [tau-0.25, tau)). See tasks.llm_judge.
    """
    if a is None or b is None:
        both_absent = a is None and b is None
        return both_absent, (1.0 if both_absent else 0.0), "absent"

    # Action: controlled vocabulary → name-exact only.
    if is_action:
        return (_norm_name(a["name"]) == _norm_name(b["name"])), 1.0, "action_name"

    # 1. IRI exact
    if _iri_match(a, b):
        return True, 1.0, "iri"
    # 2. Name exact
    if _norm_name(a["name"]) == _norm_name(b["name"]):
        return True, 1.0, "name"
    # 3. Embedding
    if embedder is not None:
        sim = embedder.cosine(a["name"], b["name"])
        if sim >= tau:
            return True, sim, "embedding"
        # 4. Judge — only for borderline cases when a judge is supplied
        if judge is not None and (tau - 0.25) <= sim < tau:
            verdict = judge(a["name"], b["name"])
            if verdict is not None:
                return verdict, sim, "judge"
        return False, sim, "embedding"
    return False, 0.0, "none"


def triple_match(pred: dict, ref: dict, *, embedder: Optional[Embedder] = None,
                 tau: float = DEFAULT_TAU, judge=None) -> tuple[bool, float, str]:
    """
    Match a predicted triple to a reference triple.
    Rule: action must match AND at least one of {object, process} must match.
    Returns (matched, score, method) where score = mean slot similarity and
    method names the tier that decided the object/process match.
    """
    p, r = normalise_triple(pred), normalise_triple(ref)

    obj_ok, obj_sim, obj_m = match_slot(p["object"],  r["object"],  is_action=False,
                                        embedder=embedder, tau=tau, judge=judge)
    prc_ok, prc_sim, prc_m = match_slot(p["process"], r["process"], is_action=False,
                                        embedder=embedder, tau=tau, judge=judge)
    act_ok, act_sim, act_m = match_slot(p["action"],  r["action"],  is_action=True,
                                        embedder=embedder, tau=tau)

    # A vacuous "both slots absent" is compatible but is NOT positive evidence:
    # require a *real* (non-absent) match on the action AND on ≥1 of object/process.
    def _real(ok, method):
        return ok and method != "absent"

    matched = _real(act_ok, act_m) and (_real(obj_ok, obj_m) or _real(prc_ok, prc_m))
    score = (obj_sim + prc_sim + act_sim) / 3.0
    method = obj_m if _real(obj_ok, obj_m) else (prc_m if _real(prc_ok, prc_m) else "none")
    return matched, score, method


def match_ke(pred_triples: list[dict], ref_triples: list[dict], *,
             embedder: Optional[Embedder] = None, tau: float = DEFAULT_TAU,
             judge=None) -> list[dict]:
    """
    For each predicted triple, find its best match among the reference triples
    (greedy, 1-to-1: each reference triple is consumed once). Returns one record
    per predicted triple: {matched, score, method, ref_idx}.
    """
    used: set = set()
    results = []
    for p in pred_triples:
        best = {"matched": False, "score": 0.0, "method": "none", "ref_idx": None}
        for j, r in enumerate(ref_triples):
            if j in used:
                continue
            matched, score, method = triple_match(p, r, embedder=embedder,
                                                   tau=tau, judge=judge)
            if score > best["score"]:
                best = {"matched": matched, "score": score, "method": method, "ref_idx": j}
        if best["matched"] and best["ref_idx"] is not None:
            used.add(best["ref_idx"])
        results.append(best)
    return results


# ---------------------------------------------------------------------------
# Cross-model clustering (consensus primitive)
# ---------------------------------------------------------------------------

def cluster_ke_triples(model_to_triples: dict, *, embedder: Optional[Embedder] = None,
                       tau: float = DEFAULT_TAU, judge=None) -> list[dict]:
    """
    Cluster equivalent triples ACROSS models for a single KE.

    model_to_triples : {model_key: [triple, ...]}  (the model's LLM triples for this KE)
    Returns clusters, each:
      {"representative": <triple>, "members": [(model_key, idx), ...],
       "support": [model_key, ...]}   # distinct models that produced an equivalent triple

    Greedy single-link: a triple joins the first existing cluster whose representative it
    matches (triple_match), else starts a new cluster. `support` size is the consensus count.
    """
    clusters: list[dict] = []
    for model, triples in model_to_triples.items():
        for idx, t in enumerate(triples):
            placed = False
            for c in clusters:
                matched, _, _ = triple_match(t, c["representative"],
                                             embedder=embedder, tau=tau, judge=judge)
                if matched:
                    c["members"].append((model, idx))
                    if model not in c["support"]:
                        c["support"].append(model)
                    placed = True
                    break
            if not placed:
                clusters.append({"representative": t, "members": [(model, idx)],
                                 "support": [model]})
    return clusters


# ---------------------------------------------------------------------------
# Report helper (used by the Ploomber task and the optional CLI below)
# ---------------------------------------------------------------------------

def iter_annotated(kec: dict):
    """Yield (ke_id, structured_triples, llm_triples) for KEs that have both."""
    for ke_id, rec in kec.items():
        structured = rec.get("structured") or []
        llm = (rec.get("llm_extracted") or {}).get("triples") or []
        if structured and llm:
            yield ke_id, structured, llm


def llm_triples(rec: dict) -> list:
    """The LLM-extracted triples of a kec.json record (empty list if absent/old schema)."""
    return (rec.get("llm_extracted") or {}).get("triples") or []


def match_report(kec: dict, *, embedder: Optional[Embedder] = None,
                 tau: float = DEFAULT_TAU) -> dict:
    """Run match_ke over all annotated KEs; return summary + per-KE matches."""
    per_ke = {}
    n_pred = n_matched = 0
    method_counts: dict = {}
    for ke_id, structured, llm in iter_annotated(kec):
        ms = match_ke(llm, structured, embedder=embedder, tau=tau)
        per_ke[ke_id] = ms
        for m in ms:
            n_pred += 1
            n_matched += int(m["matched"])
            method_counts[m["method"]] = method_counts.get(m["method"], 0) + 1
    return {
        "summary": {
            "n_ke": len(per_ke),
            "n_pred_triples": n_pred,
            "n_matched": n_matched,
            "match_rate": (n_matched / n_pred) if n_pred else None,
            "tau": tau,
            "method_counts": method_counts,
        },
        "per_ke": per_ke,
    }


if __name__ == "__main__":   # ad-hoc use; the Ploomber task is tasks/compare_kec.py
    import argparse, json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Hybrid KEC triple matcher (ad-hoc)")
    ap.add_argument("--kec", required=True)
    ap.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    ap.add_argument("--tau", type=float, default=DEFAULT_TAU)
    ap.add_argument("--no-embed", action="store_true")
    a = ap.parse_args()
    with open(a.kec, encoding="utf-8") as f:
        kec = json.load(f)
    emb = None if a.no_embed else Embedder(a.embedding_model)
    rep = match_report(kec, embedder=emb, tau=a.tau)
    print(json.dumps(rep["summary"], indent=2))
