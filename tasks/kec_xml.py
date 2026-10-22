"""
kec_xml.py — cross-model KEC consensus -> XML. LIBRARY, not a task.

Maps viz_consensus.json (tasks/viz_consensus.py: per Key Event the curated triples `gt` and
the `clusters` of model triples, each with `members` and their match against the curated
triples) onto the xsdata-pydantic classes generated from resources/kec_consensus_schema.xsd
(aopkb/kec_consensus.py, see tasks/gen_kec_bindings.py), writes the document and validates
it. Same structure as tasks/assay_xml.py, whose prefix map and helpers it reuses.

A separate document type: the consensus is not curated knowledge, so it is NOT written into
<biological-event> (aop.xsd is frozen and untouched). Key Events are referenced by their
AOP-Wiki GUID; the stable numbers live only in <lookup>.

    <kec-consensus>
      <summary>...</summary>
      <terms><term id kind source source-id iri><name/></term>*</terms>
      <key-event-result ke-id>   <curated-triple index status object-id process-id action-id/>*
      <consensus-triple id ke-id status support models object-id process-id action-id ...>
        <member model object-id process-id action-id matched curated-index method score/>*
      <lookup><key-event-reference id aop-wiki-id title/>*</lookup>
    </kec-consensus>

The statuses are not in viz_consensus.json; derive_status() ports the logic of the viz_consensus
renderer (keBlock / clusterSummary) with one addition, the acceptance threshold: only clusters
with support >= min_support_ensemble are exported and can confirm a curated triple.
Slots stay {name, iri, source} dicts (the wire format is frozen); slot logprobs are not
exported.

Everything lossy is counted in a KecXmlReport (clusters below the threshold, Key Events
without an AOP-Wiki GUID, action-less clusters, unknown prefixes, ...).

Import-safe (no module-level side effects), like tasks/assay_xml.py.
"""
from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from lxml import etree
from xsdata.formats.dataclass.serializers.config import SerializerConfig
from xsdata_pydantic.bindings import XmlSerializer

from aopkb.kec_consensus import (
    ClusterStatusEnum,
    CuratedStatusEnum,
    KecConsensus,
    TermKindEnum,
)
# the generic parts of the assay export: prefix map, text helpers, validation, HTML
from tasks.assay_xml import (_clean, _norm, expand_iri, ke_number, render_html,
                             validate)

__all__ = ["KEC_NS", "KecXmlReport", "MAX_UNRESOLVED_FRACTION", "build_consensus",
           "check_unresolved", "crosscheck", "derive_status", "ke_uuids_from_kec",
           "render_html", "validate", "write_xml"]

KEC_NS = "http://www.aopkb.org/kec-consensus"

# Default share of Key Events that may lack an AOP-Wiki GUID before the export fails.
MAX_UNRESOLVED_FRACTION = 0.05

# Fixed namespace for the uuid5 GUIDs of terms and clusters. A constant URL, so it can be
# re-derived; changing it changes every GUID.
KEC_UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL,
                                "https://aopkb.org/aopmapper/kec-consensus")

KINDS = ("object", "process", "action")
_SHORT_ID = re.compile(r"^([A-Za-z][A-Za-z0-9]*)_([A-Za-z0-9][A-Za-z0-9._-]*)$")
_CURIE = re.compile(r"^([A-Za-z][A-Za-z0-9]*):(\S+)$")
_NULLISH = {"", "null", "none", "nan"}          # strings the extraction writes for "no value"


@dataclass
class KecXmlReport:
    """Counts of everything the export had to drop, skip or note."""
    restricted: bool = False            # the viz_consensus read was restricted to a subset
    min_support_ensemble: int = 0
    n_kes_in: int = 0                   # Key Events in viz_consensus
    n_kes: int = 0                      # exported
    n_clusters_in: int = 0
    n_clusters_below_threshold: int = 0
    n_clusters: int = 0                 # exported (accepted)
    n_clusters_confirmed: int = 0
    n_clusters_novel: int = 0
    n_members: int = 0
    n_curated_triples: int = 0
    n_curated_confirmed: int = 0
    n_curated_missed: int = 0
    n_terms: Counter = field(default_factory=Counter)             # kind -> n
    terms_without_iri: Counter = field(default_factory=Counter)   # kind -> n
    terms_with_name_variants: int = 0
    unknown_prefixes: Counter = field(default_factory=Counter)    # prefix -> n (terms)
    # exported without the slot / not exported at all
    clusters_without_object: int = 0
    clusters_without_process: int = 0
    clusters_without_action: int = 0
    members_without_action: int = 0
    kes_without_number: list = field(default_factory=list)        # ke_id is not "KE<n>"
    kes_without_uuid: list = field(default_factory=list)          # KE has no AOP-Wiki GUID
    kes_empty: int = 0                  # no curated triples and no accepted cluster
    refs_out_of_range: int = 0          # match points past the curated triples of the KE
    guid_collisions: int = 0            # same (KE, representative triple): ordinal suffix
    stripped_control_chars: int = 0
    validation_failures: int = 0
    validation_errors: list = field(default_factory=list)         # first few messages
    crosscheck_failures: int = 0        # lxml cross-check (kinds, statuses, counters)
    crosscheck_errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "restricted": self.restricted,
            "min_support_ensemble": self.min_support_ensemble,
            "n_kes_in": self.n_kes_in,
            "n_kes": self.n_kes,
            "n_clusters_in": self.n_clusters_in,
            "n_clusters_below_threshold": self.n_clusters_below_threshold,
            "n_clusters": self.n_clusters,
            "n_clusters_confirmed": self.n_clusters_confirmed,
            "n_clusters_novel": self.n_clusters_novel,
            "n_members": self.n_members,
            "n_curated_triples": self.n_curated_triples,
            "n_curated_confirmed": self.n_curated_confirmed,
            "n_curated_missed": self.n_curated_missed,
            "n_terms": dict(self.n_terms),
            "terms_without_iri": dict(self.terms_without_iri),
            "terms_with_name_variants": self.terms_with_name_variants,
            "unknown_prefixes": dict(self.unknown_prefixes),
            "clusters_without_object": self.clusters_without_object,
            "clusters_without_process": self.clusters_without_process,
            "clusters_without_action": self.clusters_without_action,
            "members_without_action": self.members_without_action,
            "kes_without_number": self.kes_without_number,
            "kes_without_uuid": self.kes_without_uuid,
            "kes_empty": self.kes_empty,
            "refs_out_of_range": self.refs_out_of_range,
            "guid_collisions": self.guid_collisions,
            "stripped_control_chars": self.stripped_control_chars,
            "validation_failures": self.validation_failures,
            "validation_errors": self.validation_errors[:20],
            "crosscheck_failures": self.crosscheck_failures,
            "crosscheck_errors": self.crosscheck_errors[:20],
        }


# ── KE lookup ─────────────────────────────────────────────────────────────────

def ke_uuids_from_kec(kecs: Iterable[dict]) -> dict[str, str]:
    """The per-model kec.json contents ({ke_key: record}) -> {"KE123": AOP-Wiki GUID}.

    extract_kec writes `ke_id` ("KE<n>") and `uuid` (the AOP-Wiki GUID) on every record, from
    the AOP-Wiki release the extraction ran on, so the mapping always matches the KEC. The
    models of a panel were extracted from the same release; the first non-empty uuid per KE
    wins (no cross-model check)."""
    out: dict[str, str] = {}
    for kec in kecs:
        for key, rec in (kec or {}).items():
            if not isinstance(rec, dict):
                continue
            ke_id = str(rec.get("ke_id") or key or "").strip()
            ke_uuid = str(rec.get("uuid") or "").strip()
            if ke_id and ke_uuid and ke_id not in out:
                out[ke_id] = ke_uuid
    return out


def check_unresolved(report: KecXmlReport, max_fraction: float = MAX_UNRESOLVED_FRACTION) -> None:
    """Raise ValueError when the share of Key Events without an AOP-Wiki GUID
    (report.kes_without_uuid / report.n_kes_in) exceeds `max_fraction`. A few unresolved KEs
    (a KE the extraction skipped) are tolerated and listed in the report; many mean the kec.json
    of the wrong release or the wrong products folder."""
    n_in = report.n_kes_in
    n_bad = len(report.kes_without_uuid)
    if n_in and n_bad / n_in > float(max_fraction):
        raise ValueError(
            f"{n_bad} of {n_in} Key Events ({n_bad / n_in:.1%}) have no AOP-Wiki GUID in the "
            f"kec.json of the panel, more than the allowed {float(max_fraction):.1%}; first: "
            f"{report.kes_without_uuid[:5]}")


# ── status derivation (port of the viz_consensus renderer) ────────────────────

@dataclass
class KeStatus:
    """Derived status of one Key Event. Cluster positions index ke["clusters"], curated
    positions index ke["gt"]."""
    accepted: list[int]                 # clusters with support >= threshold, input order
    refs: dict[int, list[int]]          # accepted cluster -> curated triples it confirms
    curated: list[str]                  # per curated triple: "confirmed" | "missed"
    n_below: int = 0                    # clusters below the threshold (not exported)
    n_out_of_range: int = 0             # matches pointing past the curated triples (ignored)

    def cluster_status(self, ci: int) -> str:
        return "confirmed" if self.refs.get(ci) else "novel"


def _matches(cluster: dict):
    for mem in cluster.get("members") or []:
        v = mem.get("match")
        if v and v.get("matched"):
            yield v


def best_match(cluster: dict) -> tuple[Optional[str], Optional[float]]:
    """(method, score) of the cluster as the viz_consensus summary line shows it: the first
    matched member sets it, and every later `iri` match replaces it; (None, None) when no
    member matched."""
    method, score = None, None
    for v in _matches(cluster):
        if method is None or v.get("method") == "iri":
            method, score = v.get("method"), v.get("score")
    return method, score


def derive_status(ke: dict, min_support: int = 1) -> KeStatus:
    """A cluster confirms a curated triple when one of its members matched it (matched and
    ref_idx set); a curated triple is confirmed when some accepted cluster confirms it, else
    missed; an accepted cluster confirming nothing is novel. Clusters with support below
    `min_support` are not accepted: they confirm nothing and are not exported."""
    n_gt = len(ke.get("gt") or [])
    clusters = ke.get("clusters") or []
    accepted = [ci for ci, c in enumerate(clusters)
                if int(c.get("support") or 0) >= min_support]
    refs: dict[int, list[int]] = {}
    out_of_range = 0
    for ci in accepted:
        idx = set()
        for v in _matches(clusters[ci]):
            r = v.get("ref_idx")
            if r is None:
                continue
            if 0 <= int(r) < n_gt:
                idx.add(int(r))
            else:
                out_of_range += 1
        refs[ci] = sorted(idx)
    confirmed = {r for rs in refs.values() for r in rs}
    return KeStatus(
        accepted=accepted, refs=refs,
        curated=["confirmed" if gi in confirmed else "missed" for gi in range(n_gt)],
        n_below=len(clusters) - len(accepted), n_out_of_range=out_of_range)


# ── terms ─────────────────────────────────────────────────────────────────────

def term_guid(kind: str, source: str, source_id: str, name: str) -> str:
    """Deterministic uuid5 over (slot kind, ontology, normalised id); over the case-folded
    name when there is no id. The same term is the same object for every KE and model."""
    ident = (source_id.casefold() if source_id else f"|{_norm(name)}")
    return str(uuid.uuid5(KEC_UUID_NAMESPACE, f"term|{kind}|{source}|{ident}"))


def normalise_slot(slot: dict) -> tuple[str, str, Optional[str]]:
    """{name, iri, source} -> (source, source-id, iri), '' / None where unknown.
    The id is the short form (GO_0006954; a CURIE HP:0012649 or a full IRI is reduced to it);
    the ontology is the id's prefix when it has one, else the slot's `source` upper-cased
    (WIKI for the AOP-Wiki action ids "1", "2"). An IRI is kept as given when it is one, and
    expanded from the short form through the shared prefix map otherwise."""
    raw = str(slot.get("iri") or "").strip()
    if raw.casefold() in _NULLISH:
        raw = ""
    source = str(slot.get("source") or "").strip().upper()
    if source.casefold() in _NULLISH:
        source = ""
    iri = raw if re.match(r"https?://", raw) else None
    sid = re.split(r"[/#]", raw)[-1] if iri else raw
    m = _CURIE.match(sid)
    if m:
        sid = f"{m.group(1)}_{m.group(2)}"
    m = _SHORT_ID.match(sid)
    if m:
        source, sid = m.group(1).upper(), f"{m.group(1).upper()}_{m.group(2)}"
        if iri is None:
            expanded = expand_iri(sid)
            iri = expanded[0] if expanded else None
    return source, sid, iri


class _Terms:
    """Registry of the terms met while building: guid -> kind, source, id, iri, names."""

    def __init__(self, report: KecXmlReport):
        self.report = report
        self.entries: dict[str, dict] = {}

    def ref(self, kind: str, slot: Optional[dict]) -> Optional[str]:
        """GUID of the slot's term, None when the slot is empty."""
        name = _clean((slot or {}).get("name"), self.report)
        if not name:
            return None
        source, sid, iri = normalise_slot(slot)
        gid = term_guid(kind, source, sid, name)
        e = self.entries.setdefault(gid, {
            "kind": kind, "source": source, "source_id": sid, "iris": set(),
            "names": Counter(), "raw": str((slot or {}).get("iri") or "")})
        e["names"][name] += 1
        if iri:
            e["iris"].add(iri)
        return gid

    def objects(self) -> list[KecConsensus.Terms.Term]:
        out = []
        for gid, e in self.entries.items():
            names = sorted(e["names"], key=lambda n: (-e["names"][n], n.casefold(), n))
            if len(names) > 1:
                self.report.terms_with_name_variants += 1
            iri = min(e["iris"]) if e["iris"] else None
            self.report.n_terms[e["kind"]] += 1
            if iri is None:
                self.report.terms_without_iri[e["kind"]] += 1
                m = _SHORT_ID.match(e["source_id"] or "")
                if m:       # an id of an ontology outside the prefix map (assay_xml)
                    self.report.unknown_prefixes[m.group(1).upper()] += 1
            out.append(KecConsensus.Terms.Term(
                id=gid, kind=TermKindEnum(e["kind"]), name=names[0],
                source=e["source"] or None, source_id=e["source_id"] or None, iri=iri))
        return sorted(out, key=lambda t: (KINDS.index(t.kind.value), t.source or "",
                                          (t.source_id or "").casefold(), _norm(t.name), t.id))


# ── build ─────────────────────────────────────────────────────────────────────

def _model(text, report: KecXmlReport) -> str:
    return re.sub(r"\s+", "_", _clean(text, report))


def _ke_sort(ke: dict):
    n = ke_number(ke.get("ke_id"))
    return (n if n is not None else 10**9, str(ke.get("ke_id") or ""))


def build_consensus(viz: dict, ke_uuids: dict[str, str], min_support_ensemble: int = 1,
                    report: Optional[KecXmlReport] = None) -> KecConsensus:
    """viz_consensus.json (+ {"KE<n>": wiki GUID}) -> the `KecConsensus` root. Output order
    is by Key Event number, support (descending) and the representative triple, so it does not
    depend on the input order.

    A Key Event is exported only with a "KE<n>" id and an AOP-Wiki GUID (the others are listed
    in the report) and when it has curated triples or accepted clusters."""
    report = report if report is not None else KecXmlReport()
    summ = viz.get("summary") or {}
    kes_in = viz.get("kes") or []
    report.restricted = bool(summ.get("restricted"))
    report.min_support_ensemble = int(min_support_ensemble)
    report.n_kes_in = len(kes_in)
    report.n_clusters_in = sum(len(k.get("clusters") or []) for k in kes_in)

    terms = _Terms(report)
    key_results: list[KecConsensus.KeyEventResult] = []
    triples: list[KecConsensus.ConsensusTriple] = []  # by KE number, then support (descending)
    ke_refs: dict[str, tuple[int, str]] = {}        # exported KE GUID -> (number, title)

    for ke in sorted(kes_in, key=_ke_sort):
        ke_id = str(ke.get("ke_id") or "")
        number = ke_number(ke_id)
        if number is None:
            report.kes_without_number.append(ke_id)
            continue
        ke_uuid = str(ke_uuids.get(ke_id) or "").strip()
        if not ke_uuid:
            report.kes_without_uuid.append(ke_id)
            continue
        st = derive_status(ke, int(min_support_ensemble))
        gt = ke.get("gt") or []
        report.n_clusters_below_threshold += st.n_below
        report.refs_out_of_range += st.n_out_of_range
        if not gt and not st.accepted:
            report.kes_empty += 1
            continue

        curated = []
        for gi, g in enumerate(gt):
            curated.append(KecConsensus.KeyEventResult.CuratedTriple(
                index=gi, status=CuratedStatusEnum(st.curated[gi]),
                object_id=terms.ref("object", g.get("object")),
                process_id=terms.ref("process", g.get("process")),
                action_id=terms.ref("action", g.get("action"))))
        n_conf = st.curated.count("confirmed")
        n_novel = sum(1 for ci in st.accepted if not st.refs[ci])
        key_results.append(KecConsensus.KeyEventResult(
            curated_triple=curated, ke_id=ke_uuid, n_curated=len(gt),
            n_confirmed=n_conf, n_novel=n_novel))
        report.n_curated_triples += len(gt)
        report.n_curated_confirmed += n_conf
        report.n_curated_missed += len(gt) - n_conf
        title = _clean(ke.get("title"), report)
        ke_refs[ke_uuid] = (number, "" if title == ke_id else title)

        seen: Counter = Counter()
        built = []
        for ci in st.accepted:
            c = ke["clusters"][ci]
            rep = c.get("rep") or {}
            obj, proc, act = (terms.ref(k, rep.get(k)) for k in KINDS)
            members = []
            for mem in c.get("members") or []:
                slots = mem.get("slots") or {}
                v = mem.get("match")
                matched = bool(v.get("matched")) if v else None
                ref = v.get("ref_idx") if v and matched else None
                if ref is not None and not 0 <= int(ref) < len(gt):
                    ref = None
                m_obj, m_proc, m_act = (terms.ref(k, slots.get(k)) for k in KINDS)
                report.members_without_action += m_act is None
                members.append(KecConsensus.ConsensusTriple.Member(
                    model=_model(mem.get("model"), report) or "unknown",
                    object_id=m_obj, process_id=m_proc, action_id=m_act,
                    matched=matched,
                    curated_index=int(ref) if ref is not None else None,
                    method=(v.get("method") if v else None) or None,
                    score=v.get("score") if v else None))
            members.sort(key=lambda m: (m.model, m.object_id or "", m.process_id or "",
                                        m.action_id or "", str(m.matched),
                                        m.curated_index if m.curated_index is not None else -1,
                                        m.method or "", m.score if m.score is not None else -1))
            models = sorted({_model(m, report) for m in c.get("models") or []
                             if _model(m, report)})
            method, score = best_match(c)
            refs = st.refs[ci]
            report.clusters_without_object += obj is None
            report.clusters_without_process += proc is None
            report.clusters_without_action += act is None
            built.append(((-int(c.get("support") or 0), obj or "", proc or "", act or "",
                           models, [(m.model, m.object_id or "", m.process_id or "",
                                     m.action_id or "",
                                     -1 if m.curated_index is None else m.curated_index)
                                    for m in members]),
                          dict(rep=(obj, proc, act), members=members, models=models,
                               support=int(c.get("support") or 0), refs=refs,
                               method=method if refs else None,
                               score=score if refs else None)))
        built.sort(key=lambda b: b[0])
        for _, b in built:
            obj, proc, act = b["rep"]
            seen[b["rep"]] += 1
            if seen[b["rep"]] > 1:
                report.guid_collisions += 1
            gid = cluster_guid(ke_uuid, b["rep"], seen[b["rep"]])
            report.n_members += len(b["members"])
            report.n_clusters_confirmed += bool(b["refs"])
            report.n_clusters_novel += not b["refs"]
            triples.append(KecConsensus.ConsensusTriple(
                member=b["members"], id=gid, ke_id=ke_uuid,
                status=ClusterStatusEnum("confirmed" if b["refs"] else "novel"),
                support=b["support"], models=b["models"],
                object_id=obj, process_id=proc, action_id=act,
                curated_indices=b["refs"], method=b["method"], score=b["score"]))

    report.n_kes = len(key_results)
    report.n_clusters = len(triples)

    hist = summ.get("support_histogram")
    if hist is None:
        hist = Counter(int(c.get("support") or 0) for k in kes_in
                       for c in k.get("clusters") or [])
    models = [_clean(m, report) for m in summ.get("models") or []]
    summary = KecConsensus.Summary(
        model=[m for m in models if m],
        support_histogram=KecConsensus.Summary.SupportHistogram(bin=[
            KecConsensus.Summary.SupportHistogram.Bin(support=int(s), clusters=int(n))
            for s, n in sorted(hist.items(), key=lambda kv: int(kv[0]))]) if hist else None,
        n_models=int(summ.get("n_models") or len([m for m in models if m])),
        restricted=report.restricted,
        n_kes=report.n_kes, n_clusters=report.n_clusters_in, n_accepted=report.n_clusters,
        min_support=summ.get("min_support"), min_support_ensemble=int(min_support_ensemble),
        tau=summ.get("tau"), embedding=_clean(summ.get("embedding"), report) or None)

    term_objs = terms.objects()
    lookup = KecConsensus.Lookup(key_event_reference=[
        KecConsensus.Lookup.KeyEventReference(id=u, aop_wiki_id=n, title=t or None)
        for u, (n, t) in sorted(ke_refs.items(), key=lambda kv: (kv[1][0], kv[0]))
    ]) if ke_refs else None
    return KecConsensus(
        summary=summary,
        terms=KecConsensus.Terms(term=term_objs) if term_objs else None,
        key_event_result=key_results, consensus_triple=triples,
        lookup=lookup)


def cluster_guid(ke_uuid: str, rep: tuple, ordinal: int = 1) -> str:
    """Deterministic uuid5 over (KE GUID, GUIDs of the representative object / process /
    action). Deterministic per input, not promised stable across releases (the cluster is
    recomputed). `ordinal` > 1 only separates clusters with the same representative triple."""
    key = f"cluster|{str(ke_uuid).strip().lower()}|" + "|".join(r or "" for r in rep)
    if ordinal > 1:
        key += f"|{ordinal}"
    return str(uuid.uuid5(KEC_UUID_NAMESPACE, key))


# ── serialisation, validation, cross-check ────────────────────────────────────

def to_xml(consensus: KecConsensus) -> str:
    """Pretty-printed XML text, everything in the kec-consensus namespace."""
    cfg = SerializerConfig(indent="  ", xml_declaration=True, encoding="UTF-8")
    return XmlSerializer(config=cfg).render(consensus, ns_map={None: KEC_NS})


def write_xml(consensus: KecConsensus, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_xml(consensus), encoding="utf-8")
    return path


def crosscheck(xml_path, report: Optional[KecXmlReport] = None) -> list[str]:
    """Independent lxml check of what the XSD cannot say: every slot reference resolves to a
    term of the right kind, every status agrees with the curated indices, and the counters
    of each Key Event agree with its triples. Returns the problems (empty = consistent); when
    a report is given, also records their count and the first messages."""
    ns = {"c": KEC_NS}
    root = etree.parse(str(xml_path)).getroot()
    kind = {t.get("id"): t.get("kind") for t in root.iterfind("c:terms/c:term", ns)}
    problems: list[str] = []

    def check_slots(el, where):
        for k in KINDS:
            ref = el.get(f"{k}-id")
            if ref is not None and kind.get(ref) != k:
                problems.append(f"{where}: {k}-id {ref} is a {kind.get(ref)!r} term")

    for el in root.iterfind("c:consensus-triple", ns):
        where = f"consensus-triple {el.get('id')}"
        check_slots(el, where)
        for m in el.iterfind("c:member", ns):
            check_slots(m, f"{where} member")
        if int(el.get("support")) < int(root.find("c:summary", ns).get("min-support-ensemble")):
            problems.append(f"{where}: support below the acceptance threshold")
    by_ke: dict[str, list] = {}
    for el in root.iterfind("c:consensus-triple", ns):
        by_ke.setdefault(el.get("ke-id"), []).append(el)
    for res in root.iterfind("c:key-event-result", ns):
        where = f"key-event-result {res.get('ke-id')}"
        ctr = res.findall("c:curated-triple", ns)
        for c in ctr:
            check_slots(c, where)
        clusters = by_ke.get(res.get("ke-id"), [])
        confirmed_idx = {int(i) for cl in clusters for i in (cl.get("curated-indices") or "").split()}
        if {int(c.get("index")) for c in ctr if c.get("status") == "confirmed"} != confirmed_idx:
            problems.append(f"{where}: curated statuses disagree with the clusters")
        if int(res.get("n-curated")) != len(ctr):
            problems.append(f"{where}: n-curated")
        if int(res.get("n-confirmed")) != len(confirmed_idx):
            problems.append(f"{where}: n-confirmed")
        if int(res.get("n-novel")) != sum(1 for cl in clusters if cl.get("status") == "novel"):
            problems.append(f"{where}: n-novel")
    if report is not None:
        report.crosscheck_failures = len(problems)
        report.crosscheck_errors = problems[:20]
    return problems
