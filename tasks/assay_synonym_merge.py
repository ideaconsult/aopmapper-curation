"""
assay_synonym_merge.py — assay-technique name-merge logic shared by
tasks/assay_synonyms_update.py (Ploomber task, runs on each release) and
scripts/propose_technique_merges.py (thin CLI for human review). Import-safe library.

Pipeline: normalise names (tasks.assay_names.punct_normalize) -> embed the unique
normalised names (an injected `embed(list[str]) -> L2-normalised matrix`; the real one wraps
tasks.kec_compare.Embedder, SapBERT) -> all pairs with cosine >= min_sim -> per-pair
features (Jaccard, `risky` flag) -> hard VETO (digit/roman numeral, Greek/subunit,
antonym differences are never merged) -> STAR grouping against the current synonym map
(plan_merges). Nothing here touches files except write_synonyms / append_applied.

The risky flag marks a pair whose names differ by a distinguishing token (a digit, or a
short alphanumeric token on one side only: RT-PCR vs qPCR, ELISA vs ELISPOT, HPLC vs UPLC,
OECD TG numbers). It is recorded, not used to exclude, when merges are applied.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from collections import Counter
from pathlib import Path

import numpy as np

from tasks.assay_names import punct_normalize

BAND_WIDTH = 0.05
SHORT_LEN = 6
# Function words / generic nouns that never distinguish a technique ("X assay" vs "X").
# Applied to the one-sided-token test only; Jaccard still sees them.
NEUTRAL_TOKENS = frozenset({
    "and", "with", "of", "for", "the", "in", "on", "by", "using", "based", "or",
    "assay", "assays", "test", "tests", "analysis", "analyses", "measurement",
    "measurements", "determination", "method", "detection", "level", "levels",
})
COLUMNS = ["name_a", "name_b", "sim", "jaccard", "risky",
           "n_ke_a", "n_ke_b", "n_rec_a", "n_rec_b"]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_techniques(docs: list[dict]) -> list[dict]:
    """assay_technique docs -> [{id, name, norm, n_ke, n_rec}], sorted by (norm, id).
    n_ke = distinct KEs on the technique doc; n_rec = ke_assay docs pointing at it."""
    n_rec = Counter(d["technique_id_s"] for d in docs if d.get("type_s") == "ke_assay")
    out = []
    for d in docs:
        if d.get("type_s") != "assay_technique":
            continue
        out.append({
            "id": d["id"], "name": d["name_t"], "norm": punct_normalize(d["name_t"]),
            "n_ke": len(set(d.get("ke_ids_ss", []))), "n_rec": n_rec.get(d["id"], 0),
        })
    out.sort(key=lambda t: (t["norm"], t["id"]))
    return out


# ---------------------------------------------------------------------------
# Pair features
# ---------------------------------------------------------------------------

def tokens(norm: str) -> set[str]:
    return set(norm.split())


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0


def _stem_related(tok: str, others: set[str]) -> bool:
    """True if tok is a prefix-variant of a token on the other side (blot/blotting,
    assay/assays) - an inflection, not a distinguishing token."""
    return any(o.startswith(tok) or tok.startswith(o) for o in others)


def is_risky(norm_a: str, norm_b: str, short_len: int = SHORT_LEN) -> bool:
    """The names differ by a distinguishing token: among tokens present on one side only
    (ignoring prefix-inflections of a token on the other side and the generic words in
    NEUTRAL_TOKENS), any contains a digit or is a short (<= short_len) alphanumeric
    token."""
    ta, tb = tokens(norm_a) - NEUTRAL_TOKENS, tokens(norm_b) - NEUTRAL_TOKENS
    only_a, only_b = ta - tb, tb - ta
    for toks, others in ((only_a, only_b), (only_b, only_a)):
        for t in toks:
            numeral = re.search(r"\d", t) or t in ROMAN      # never an inflection
            if not numeral and _stem_related(t, others):
                continue
            if re.search(r"\d", t) or (len(t) <= short_len and re.fullmatch(r"[a-z0-9]+", t)):
                return True
    return False


# ---------------------------------------------------------------------------
# Hard veto: pairs that are never merged, whatever their cosine or risky flag
# ---------------------------------------------------------------------------

ROMAN = frozenset({"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"})
GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
         "ζ": "zeta", "θ": "theta", "κ": "kappa", "λ": "lambda", "μ": "mu", "σ": "sigma",
         "τ": "tau", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega", "ρ": "rho"}
GREEK_NAMES = frozenset(GREEK.values())
# Long names are also found glued to another word (PPARgamma); short ones only as tokens.
GREEK_GLUED = frozenset(n for n in GREEK_NAMES if len(n) >= 4)
ANTONYM_PAIRS = frozenset(frozenset(p) for p in [
    ("vitro", "vivo"), ("ex", "in"), ("positive", "negative"), ("increase", "decrease"),
    ("increased", "decreased"), ("up", "down"), ("upregulation", "downregulation"),
    ("high", "low"), ("forward", "reverse"), ("acute", "chronic"), ("early", "late"),
    ("agonist", "antagonist"), ("activation", "inhibition"), ("pre", "post"),
])
NEGATION_PREFIXES = ("in", "non", "un", "anti")
NEGATION_TOKENS = frozenset({"non", "anti", "not", "no", "without", "un"})
_NO_NEGATION_STEM = frozenset({"vitro", "vivo", "situ", "silico"})
VETO_CATEGORIES = ("greek_subunit", "digit_numeral", "antonym")


def _clean(tok: str) -> str:
    return re.sub(r"[^0-9a-z]", "", tok)


def _greek_tokens(norm: str) -> list[str]:
    """Tokens with unicode Greek letters split out as their spelled name."""
    for sym, name in GREEK.items():
        norm = norm.replace(sym, f" {name} ")
    return norm.split()


def _greek_set(toks: set[str]) -> set[str]:
    return ({t for t in toks if t in GREEK_NAMES}
            | {n for n in GREEK_GLUED if any(n in t for t in toks)})


def veto_reason(norm_a: str, norm_b: str) -> str | None:
    """Category of the hard veto that forbids merging these two normalised names, or None.

    greek_subunit: the sets of Greek-letter tokens (alpha/beta/..., or α/β/...) differ, or a
        single-letter-with-digit subunit token (A1, B2, ...) is on one side only.
    digit_numeral: the digit runs of the two names differ (TG 455 vs TG 487, IL-6 vs IL-8),
        or a roman-numeral token (i..x) is on one side only (Complex I vs Complex II), or
        two digit-bearing tokens share their digits but not their letters (CYP1A vs CYP1B).
    antonym: a one-sided token is the negation/antonym of one on the other side
        (in-/non-/un-/anti- prefix: direct vs indirect; vitro vs vivo; positive vs
        negative; increase vs decrease; up vs down ...)."""
    ga, gb = _greek_tokens(norm_a), _greek_tokens(norm_b)
    ta, tb = {_clean(t) for t in ga} - {""}, {_clean(t) for t in gb} - {""}
    only_a, only_b = ta - tb, tb - ta
    if (_greek_set(ta) != _greek_set(tb)
            or any(re.fullmatch(r"[a-z]\d+", t) for t in only_a | only_b)):
        return "greek_subunit"
    if (sorted(re.findall(r"\d+", " ".join(ga)))
            != sorted(re.findall(r"\d+", " ".join(gb)))):
        return "digit_numeral"
    if any(t in ROMAN for t in only_a | only_b):
        return "digit_numeral"
    da = {t for t in only_a if re.search(r"\d", t) and re.search(r"[a-z]", t)}
    db = {t for t in only_b if re.search(r"\d", t) and re.search(r"[a-z]", t)}
    if da and db and not any(x.startswith(y) or y.startswith(x) for x in da for y in db):
        return "digit_numeral"
    for x in only_a:
        for y in only_b:
            if frozenset((x, y)) in ANTONYM_PAIRS:
                return "antonym"
            for s, l in ((x, y), (y, x)):
                if (len(s) >= 3 and s not in _NO_NEGATION_STEM
                        and any(l == p + s for p in NEGATION_PREFIXES)):
                    return "antonym"
    if (only_a | only_b) & NEGATION_TOKENS:
        return "antonym"
    return None


# ---------------------------------------------------------------------------
# Pair search
# ---------------------------------------------------------------------------

def find_pairs(vecs: np.ndarray, min_sim: float, chunk: int = 512
               ) -> list[tuple[int, int, float]]:
    """All (i, j, cos) with i < j and cos >= min_sim. Row-chunked matrix product, so peak
    memory is chunk x n floats, not n x n. Rows are L2-normalised vectors."""
    n = len(vecs)
    pairs: list[tuple[int, int, float]] = []
    for s in range(0, n, chunk):
        sims = vecs[s:s + chunk] @ vecs.T
        rows, cols = np.nonzero(sims >= min_sim)
        for r, c in zip(rows.tolist(), cols.tolist()):
            i = s + r
            if i < c:
                pairs.append((i, c, float(sims[r, c])))
    return pairs


def propose(techs: list[dict], embed, min_sim: float = 0.80,
            short_len: int = SHORT_LEN) -> list[dict]:
    """Candidate rows (dicts keyed by COLUMNS), sorted by sim desc then names.
    `embed(list[str]) -> (n, d) array of L2-normalised vectors` is injected."""
    uniq = sorted({t["norm"] for t in techs})
    vecs = np.asarray(embed(uniq), dtype=np.float32)
    by_norm: dict[str, list[dict]] = {}
    for t in techs:
        by_norm.setdefault(t["norm"], []).append(t)

    # unique-name pairs, then expanded to technique pairs (techniques sharing a normalised
    # name are themselves a sim=1.0 pair)
    cand: list[tuple[dict, dict, float]] = []
    for i, j, s in find_pairs(vecs, min_sim):
        for a in by_norm[uniq[i]]:
            for b in by_norm[uniq[j]]:
                cand.append((a, b, s))
    for group in by_norm.values():
        for x in range(len(group)):
            for y in range(x + 1, len(group)):
                cand.append((group[x], group[y], 1.0))

    rows = []
    for a, b, s in cand:
        a, b = sorted((a, b), key=lambda t: (t["name"], t["id"]))
        rows.append({
            "name_a": a["name"], "name_b": b["name"], "sim": round(s, 4),
            "jaccard": round(jaccard(tokens(a["norm"]), tokens(b["norm"])), 4),
            "risky": int(is_risky(a["norm"], b["norm"], short_len)),
            "n_ke_a": a["n_ke"], "n_ke_b": b["n_ke"],
            "n_rec_a": a["n_rec"], "n_rec_b": b["n_rec"],
            "_ids": (a["id"], b["id"]),
        })
    rows.sort(key=lambda r: (-r["sim"], r["name_a"], r["name_b"], r["_ids"]))
    return rows


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def band_starts(min_sim: float) -> list[float]:
    starts, x, k = [], min_sim, 0
    while x < 1.0 - 1e-9:
        starts.append(round(x, 4))
        k += 1
        x = min_sim + k * BAND_WIDTH
    return starts


def summarise(techs: list[dict], rows: list[dict], min_sim: float) -> list[dict]:
    """Per band start b: pairs in [b, b+0.05) (last band closed at 1.0) split by risky;
    and single-KE techniques having >=1 neighbour with sim >= b (any / non-risky only)."""
    best_any: dict[str, float] = {}
    best_safe: dict[str, float] = {}
    for r in rows:
        for tid in r["_ids"]:
            best_any[tid] = max(best_any.get(tid, 0.0), r["sim"])
            if not r["risky"]:
                best_safe[tid] = max(best_safe.get(tid, 0.0), r["sim"])
    single = [t["id"] for t in techs if t["n_ke"] == 1]
    starts = band_starts(min_sim)
    out = []
    for k, b in enumerate(starts):
        hi = starts[k + 1] if k + 1 < len(starts) else 1.0 + 1e-9
        in_band = [r for r in rows if b - 1e-9 <= r["sim"] < hi]
        out.append({
            "band_start": b,
            "pairs": len(in_band),
            "pairs_risky": sum(r["risky"] for r in in_band),
            "pairs_nonrisky": sum(1 - r["risky"] for r in in_band),
            "single_ke_with_neighbour": sum(best_any.get(t, 0.0) >= b - 1e-9 for t in single),
            "single_ke_with_nonrisky_neighbour":
                sum(best_safe.get(t, 0.0) >= b - 1e-9 for t in single),
        })
    return out


def format_summary(techs: list[dict], summary: list[dict]) -> str:
    n_single = sum(t["n_ke"] == 1 for t in techs)
    lines = [f"techniques: {len(techs)}  (single-KE: {n_single})",
             "band_start  pairs  risky  nonrisky  single_KE_nbr(>=start)  "
             "single_KE_nonrisky_nbr(>=start)"]
    for s in summary:
        lines.append(f"{s['band_start']:<10.2f}  {s['pairs']:<5d}  {s['pairs_risky']:<5d}  "
                     f"{s['pairs_nonrisky']:<8d}  {s['single_ke_with_neighbour']:<22d}  "
                     f"{s['single_ke_with_nonrisky_neighbour']}")
    return "\n".join(lines)


def write_tsv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# --apply: fold accepted candidate pairs into resources/assay_synonyms.json
# ---------------------------------------------------------------------------

DEFAULT_SYNONYMS = Path("resources/assay_synonyms.json")
DEFAULT_APPLIED = Path("resources/assay_synonyms_applied.tsv")
APPLIED_COLUMNS = ["variant", "canonical", "sim", "risky"]
REVIEW_GROUP_SIZE = 3      # groups with more members than this are flagged for review


def read_candidates(path: Path) -> list[dict]:
    """Rows of a merge_candidates.tsv, with numeric fields typed."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    for r in rows:
        r["sim"] = float(r["sim"])
        r["risky"] = int(r["risky"])
        for k in ("n_ke_a", "n_ke_b", "n_rec_a", "n_rec_b"):
            r[k] = int(r[k])
    return rows


def plan_merges(rows: list[dict], synonyms: dict[str, list[str]],
                threshold: float = 0.90, veto: bool = True) -> dict:
    """Decide merges by STAR grouping (no transitive closure, so no chaining drift).
    Pure: does not touch files. Returns
    {"synonyms": new canonical -> [variants] dict, "groups": [...], "pairs": [...]}.

    Candidate pairs: sim >= threshold ALONE (the risky flag is recorded, never used to
    exclude - a user decision). Names are
    processed existing canonicals first (so they absorb direct neighbours, whatever the
    neighbour's n_ke), then by n_ke desc, n_rec desc, alphabetical. The first unassigned name
    becomes a canonical (a name that is already a canonical in the map stays one; a name
    that is already a variant keeps its canonical and is skipped). Every still-unassigned
    candidate neighbour of that canonical, i.e. sim(canonical, name) >= threshold
    DIRECTLY, joins it. A name is assigned once; a name reachable only through another
    member is not merged; existing canonicals and existing variants are never moved.

    New canonicals / variants are written as display names; the pipeline normalises both
    sides, so only the normalised form matters for grouping."""
    norm_canon = {punct_normalize(c): c for c in synonyms}
    key_of: dict[str, str] = {}                    # normalised name -> normalised canonical
    for c, variants in synonyms.items():
        key_of[punct_normalize(c)] = punct_normalize(c)
        for v in variants:
            key_of[punct_normalize(v)] = punct_normalize(c)

    display: dict[str, str] = {}                   # normalised name -> display name
    stats: dict[str, tuple[int, int]] = {}         # normalised name -> (n_ke, n_rec)
    adj: dict[str, dict[str, tuple]] = {}          # name -> {neighbour: (sim, risky)}
    vetoed: list[dict] = []
    for r in rows:
        if r["sim"] < threshold:
            continue
        na, nb = punct_normalize(r["name_a"]), punct_normalize(r["name_b"])
        if na == nb:
            continue
        reason = veto_reason(na, nb) if veto else None
        if reason:                                 # hard veto: never merged, any sim/risky
            vetoed.append({"name_a": r["name_a"], "name_b": r["name_b"],
                           "sim": r["sim"], "category": reason})
            continue
        for n, name, ke, rec in ((na, r["name_a"], r["n_ke_a"], r["n_rec_a"]),
                                 (nb, r["name_b"], r["n_ke_b"], r["n_rec_b"])):
            if n not in display or name < display[n]:
                display[n] = name
            old = stats.get(n, (0, 0))
            stats[n] = (max(old[0], ke), max(old[1], rec))
        edge = (r["sim"], int(r["risky"]))
        for x, y in ((na, nb), (nb, na)):
            if edge > adj.setdefault(x, {}).get(y, (-1.0, 0)):
                adj[x][y] = edge

    order = sorted(adj, key=lambda n: (n not in norm_canon, -stats[n][0], -stats[n][1],
                                       display[n], n))
    rank = {n: i for i, n in enumerate(order)}

    new_syn = {c: list(v) for c, v in synonyms.items()}
    assigned: set[str] = set()
    groups, pairs = [], []
    for n in order:
        if n in assigned:
            continue
        assigned.add(n)
        if key_of.get(n, n) != n:
            continue                               # existing variant keeps its canonical
        joiners = sorted(
            (m for m, s in adj[n].items() if m not in assigned and m not in key_of),
            key=lambda m: (-adj[n][m][0], rank[m]))
        if not joiners:
            continue
        existing = n in norm_canon
        canon_name = norm_canon[n] if existing else display[n]
        assigned.update(joiners)
        have = new_syn.setdefault(canon_name, [])
        have_norm = {punct_normalize(v) for v in have} | {n}
        for m in joiners:
            if punct_normalize(display[m]) not in have_norm:
                have.append(display[m])
                have_norm.add(punct_normalize(display[m]))
        new_syn[canon_name] = sorted(have, key=lambda v: (punct_normalize(v), v))
        groups.append({
            "canonical": canon_name, "size": 1 + len(joiners),
            "variants": [display[m] for m in joiners],
            "status": "extended existing group" if existing else "new group"})
        pairs.extend({"variant": display[m], "canonical": canon_name,
                      "sim": adj[n][m][0], "risky": adj[n][m][1]}
                     for m in joiners)
    groups.sort(key=lambda g: (-g["size"], g["canonical"]))
    return {"synonyms": new_syn, "groups": groups, "pairs": pairs, "vetoed": vetoed}


def format_groups(groups: list[dict], pairs: list[dict], top: int = 20,
                  vetoed: list[dict] = ()) -> str:
    """Group counts, size distribution (canonical + variants), the largest groups and
    every merge whose pair carries the risky flag (listed separately for scanning)."""
    review = [g for g in groups if g["size"] > REVIEW_GROUP_SIZE]
    n_new = sum(g["status"] == "new group" for g in groups)
    lines = [f"groups: {len(groups)} ({n_new} new, {len(groups) - n_new} extend an "
             f"existing group); names merged as variants: "
             f"{sum(g['size'] - 1 for g in groups)}; "
             f"groups with > {REVIEW_GROUP_SIZE} members (REVIEW): {len(review)}"]
    sizes = Counter(g["size"] for g in groups)
    lines.append("group size distribution (size:groups): " + ", ".join(
        f"{s}:{n}" for s, n in sorted(sizes.items())))
    lines.append(f"largest {min(top, len(groups))} groups:")
    for g in groups[:top]:
        lines.append(f"  [{g['size']}] {g['canonical']}  <=  {g['variants']}"
                     f"  ({g['status']})")
    risky = [p for p in pairs if p["risky"]]
    lines.append(f"risky merges (sim >= threshold, applied anyway): {len(risky)}")
    for p in sorted(risky, key=lambda p: (-p["sim"], p["variant"])):
        lines.append(f"  {p['variant']} -> {p['canonical']} ({p['sim']})")
    cats = Counter(v["category"] for v in vetoed)
    lines.append(f"hard-vetoed pairs (sim >= threshold, never merged): {len(vetoed)}; by "
                 f"category: " + ", ".join(f"{c}={cats.get(c, 0)}" for c in VETO_CATEGORIES))
    for v in sorted(vetoed, key=lambda v: (v["category"], -v["sim"], v["name_a"])):
        lines.append(f"  [{v['category']}] {v['name_a']} | {v['name_b']} ({v['sim']})")
    return "\n".join(lines)


def audit_applied(path: Path) -> list[dict]:
    """Read-only scan of an applied-merges tsv for pairs a human should look at: the hard
    veto fires (should be none after --apply) or the names differ by a loose signal -
    a digit-bearing / roman / Greek token, a negation word, or a member of an antonym pair
    on one side only. Returns [{variant, canonical, sim, why}]."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    members = {w for p in ANTONYM_PAIRS for w in p} - {"in", "up", "ex"}
    out = []
    for r in rows:
        na, nb = punct_normalize(r["variant"]), punct_normalize(r["canonical"])
        ta, tb = set(_greek_tokens(na)), set(_greek_tokens(nb))
        diff = ta ^ tb
        why = []
        if veto_reason(na, nb):
            why.append("veto:" + veto_reason(na, nb))
        for t in sorted(diff):
            if re.search(r"\d", t):
                why.append(f"digit:{t}")
            elif t in ROMAN:
                why.append(f"roman:{t}")
            elif t in GREEK_NAMES or t in NEGATION_TOKENS or t in members:
                why.append(f"word:{t}")
        if why:
            out.append({"variant": r["variant"], "canonical": r["canonical"],
                        "sim": r["sim"], "why": ",".join(why)})
    return out


def write_synonyms(synonyms: dict, path: Path) -> None:
    """Same layout as the shipped file: indent 2, ASCII-escaped, trailing newline."""
    path.write_text(json.dumps(synonyms, indent=2) + "\n", encoding="utf-8")


def append_applied(pairs: list[dict], path: Path) -> int:
    """Append merges not already recorded (by variant, canonical); returns rows added."""
    seen = set()
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            seen = {(r["variant"], r["canonical"])
                    for r in csv.DictReader(f, delimiter="\t")}
    new = [p for p in pairs if (p["variant"], p["canonical"]) not in seen]
    if not new:
        return 0
    fresh = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=APPLIED_COLUMNS, delimiter="\t")
        if fresh:
            w.writeheader()
        w.writerows(new)
    return len(new)


def apply_candidates(rows: list[dict], synonyms_path: Path, applied_path: Path,
                     threshold: float, dry_run: bool) -> str:
    synonyms = json.loads(synonyms_path.read_text(encoding="utf-8"))
    plan = plan_merges(rows, synonyms, threshold)
    n_before = len(synonyms)
    report = [f"apply threshold {threshold} "
              f"dry_run={dry_run}: {len(plan['pairs'])} merges; synonym groups "
              f"{n_before} -> {len(plan['synonyms'])}",
              format_groups(plan["groups"], plan["pairs"], vetoed=plan["vetoed"])]
    if not dry_run:
        if plan["synonyms"] != synonyms:
            write_synonyms(plan["synonyms"], synonyms_path)
        added = append_applied(plan["pairs"], applied_path)
        report.append(f"wrote {synonyms_path}; {added} rows appended to {applied_path}")
    return "\n".join(report)




# ---------------------------------------------------------------------------
# Embedder (reuses tasks.kec_compare.Embedder; no second implementation)
# ---------------------------------------------------------------------------

def default_embed(model_name: str):
    """embed(list[str]) -> stacked L2-normalised vectors, backed by the SapBERT Embedder
    the cross-model assay task uses."""
    from tasks.kec_compare import Embedder
    emb = Embedder(model_name)
    return lambda names: np.stack([emb._vec(n) for n in names])


# ---------------------------------------------------------------------------
# Release update: consensus clusters -> names -> synonym map
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def techniques_from_viz(viz: dict, current_ke_ids: set[str] | None = None,
                        min_support: int = 2) -> list[dict]:
    """Per punct-normalised assay name, counted WITHOUT the synonym map (so the map this
    feeds is not an input to its own update): n_ke = distinct KEs whose consensus holds the
    name, n_rec = consensus clusters (records) carrying it. Same gates as
    assay_consensus2solr: cluster support >= min_support, KE in the current release (if
    current_ke_ids is given). Returns load_techniques-shaped dicts sorted by (norm, id);
    `name` is the most frequent surface form (ties: first alphabetically)."""
    kes: dict[str, set] = {}
    n_rec: Counter = Counter()
    forms: dict[str, Counter] = {}
    for ke in viz.get("kes", []):
        ke_id = ke.get("ke_id")
        if current_ke_ids is not None and ke_id not in current_ke_ids:
            continue
        for cluster in ke.get("clusters", []):
            if cluster.get("support", 0) < min_support:
                continue
            name = ((cluster.get("assay") or {}).get("assay_name") or "").strip()
            norm = punct_normalize(name)
            if not norm:
                continue
            kes.setdefault(norm, set()).add(ke_id)
            n_rec[norm] += 1
            forms.setdefault(norm, Counter())[name] += 1
    out = []
    for norm in sorted(kes):
        best = sorted(forms[norm].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        out.append({"id": norm, "name": best, "norm": norm,
                    "n_ke": len(kes[norm]), "n_rec": n_rec[norm]})
    return out


def run_update(techs: list[dict], embed, synonyms_path: Path, applied_path: Path,
               threshold: float = 0.90, veto: bool = True,
               short_len: int = SHORT_LEN) -> dict:
    """Candidates (cosine >= threshold) -> star grouping + veto against the current map ->
    updated synonym file (existing canonicals/variants preserved; rewritten only if
    something changed) and an append-only applied log. Idempotent. Returns a stats dict;
    logs a WARNING if the map changed so a release that moves it is visible."""
    synonyms = json.loads(Path(synonyms_path).read_text(encoding="utf-8"))
    rows = propose(techs, embed, min_sim=threshold, short_len=short_len)
    plan = plan_merges(rows, synonyms, threshold, veto=veto)
    changed = plan["synonyms"] != synonyms
    if changed:
        write_synonyms(plan["synonyms"], Path(synonyms_path))
    added = append_applied(plan["pairs"], Path(applied_path))
    # pairs[] lists this run's merges; the applied log dedups on (variant, canonical)
    cats = Counter(v["category"] for v in plan["vetoed"])
    groups = plan["groups"]
    stats = {
        "threshold": threshold, "veto": veto,
        "n_names": len(techs), "n_candidate_pairs": len(rows),
        "n_new_merges": len(plan["pairs"]), "n_applied_rows_appended": added,
        "n_new_groups": sum(g["status"] == "new group" for g in groups),
        "n_extended_groups": sum(g["status"] != "new group" for g in groups),
        "n_groups_over_review_size": sum(g["size"] > REVIEW_GROUP_SIZE for g in groups),
        "n_risky_new_merges": sum(p["risky"] for p in plan["pairs"]),
        "n_vetoed": len(plan["vetoed"]),
        "vetoed_by_category": {c: cats.get(c, 0) for c in VETO_CATEGORIES},
        "n_synonym_groups_before": len(synonyms),
        "n_synonym_groups_after": len(plan["synonyms"]),
        "new_merges": [{"variant": p["variant"], "canonical": p["canonical"],
                        "sim": p["sim"], "risky": p["risky"]} for p in plan["pairs"]],
    }
    if stats["n_new_merges"]:
        logger.warning(
            f"assay synonym map CHANGED: {stats['n_new_merges']} new merges "
            f"({stats['n_new_groups']} new groups, {stats['n_extended_groups']} extended; "
            f"{stats['n_risky_new_merges']} risky) at threshold {threshold}; "
            f"{stats['n_vetoed']} pairs vetoed {stats['vetoed_by_category']}")
    else:
        logger.info(f"assay synonym map unchanged at threshold {threshold} "
                    f"({stats['n_vetoed']} pairs vetoed {stats['vetoed_by_category']})")
    return stats
