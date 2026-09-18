"""
viz_assay_consensus.py — Ploomber task: HTML report + JSON data of cross-model assay
consensus.

The assay analogue of viz_consensus.py, with NO ground-truth axis (assays have none).
Per KE it shows the consensus assay clusters sorted by support, each with its per-model
member assays, the verbatim `evidence` spans, ontology IRIs, and the endpoint-direction
confidence margin — so a curator can see what the panel agreed on and verify it against
the source.

Like viz_consensus.py, the consolidated per-KE cluster data (support, models, the
representative AssayRecord, members) is written to product["data"] as JSON alongside
the HTML — so other tasks (tasks/assay_consensus2solr.py) can consume the same
already-KEnnn-keyed, per-KE-grouped view without re-reading crossmodel_assays' raw
consensus file and redoing the wiki-id translation.

Ploomber:
  upstream: ["crossmodel_assays"]
  product:  {"html": ..., "data": ...}
  params:   products, model_keys
"""
from __future__ import annotations

import html as _html
import json
from pathlib import Path

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["crossmodel_assays"]
product = None
enabled = True
products = "products_202606"
model_keys = []
# optional: crossmodel_track_consistency.json (D5, response_plan.md R1 §5.3) — adds a
# per-member cross-track column showing whether THIS model's own KEC-track triple for
# this KE agrees with the biological change this specific assay implies. None/missing
# just omits the column, so this viz works unchanged for assay runs with no matching
# KEC run. Written before this viz existed (crossmodel_track_consistency.py's docstring
# calls its detail TSV "curator-readable" but nothing rendered it until now).
track_consistency_json = None
# accept/drop color threshold — MUST match env.yaml's assay_min_support, the actual
# gate tasks/assay_consensus2solr.py applies before a cluster reaches the index.
# Deliberately NOT the same as crossmodel's own `min_support` (the clustering-report
# threshold, shown separately in the info line): the bar previously coloured by
# support/n_models as a fixed-fraction gradient, so on an 11-model panel a cluster at
# the actual publish gate (e.g. support=3, assay_min_support=2) still rendered red
# because 3/11 is below the gradient's amber cutoff. Binary accept/drop against the
# real gate, mirroring tasks/viz_consensus.py's equivalent fix for the KEC track.
assay_min_support = 1
# -

from tasks.aop_select import load_wiki_id_map
from tasks.logutil import setup_logging


def _load_track_consistency(path) -> dict:
    """(model_key, ke_id, assay_name) -> {matched, matched_strict, score, method} from
    crossmodel_track_consistency.json's per_ke block. {} if not supplied/found.

    per_ke[model][ke_id] is keyed by assay_name (crossmodel_track_consistency.py),
    which is the same identity clusters are built on here, so the join is direct."""
    if not path or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for mk, ke_map in (data.get("per_ke") or {}).items():
        for ke_id, by_name in (ke_map or {}).items():
            for assay_name, r in (by_name or {}).items():
                out[(mk, ke_id, assay_name)] = r
    return out


logger = setup_logging(Path(product["html"]).parent / "viz_assay_consensus.log")

_CSS = """
* { box-sizing: border-box; font-family: system-ui, sans-serif; font-size: 13px; }
body { margin: 16px; color: #222; }
h1 { font-size: 18px; margin-bottom: 4px; }
.info { font-size: 12px; color: #555; margin-bottom: 8px; }
.hist { margin-bottom: 16px; }
details { margin-bottom: 6px; border: 1px solid #ddd; border-radius: 4px; }
details > summary { cursor: pointer; padding: 6px 10px; background: #f5f5f5;
    font-weight: 600; list-style: none; display: flex; gap: 8px; align-items: center; }
details > summary::-webkit-details-marker { display: none; }
table { width: 100%; border-collapse: collapse; }
th { background: #eceff1; font-size: 11px; padding: 3px 6px; text-align: left;
     border-bottom: 2px solid #cfd8dc; }
td { padding: 3px 6px; vertical-align: top; border-bottom: 1px solid #f0f0f0; }
.rep { background: #e8f5e9; }
.ev { color: #555; font-style: italic; font-size: 11px; }
.iri { font-size: 10px; color: #666; }
.sep { color: #bbb; padding: 0 2px; }
"""


def _e(s) -> str:
    return _html.escape(str(s)) if s else ""


def _lab(k: str) -> str:
    return k.split("--", 1)[-1] if "--" in k else k


def _support_bar(support: int, n: int, min_sup: int) -> str:
    """Binary accept/drop against `min_sup` (the Solr publish gate), not a fraction-of-
    panel gradient — a bar is green iff this cluster will actually be indexed."""
    accepted = support >= min_sup
    color = "#2e7d32" if accepted else "#e65100"
    pct = int(100 * support / max(n, 1))
    tag = "" if accepted else " ✗ below min_support"
    return (f'<span style="color:{color};font-weight:700" '
            f'title="assay_min_support={min_sup}">{support}/{n}{tag}</span> '
            f'<span style="display:inline-block;width:60px;height:8px;background:#eee;'
            f'border-radius:3px;vertical-align:middle"><span style="display:block;'
            f'width:{pct}%;height:100%;background:{color};border-radius:3px"></span></span>')


def _fmt_kec_triple(t: dict | None) -> str:
    """'object / process / action' names for an assay's own kec_triple slot
    (AssayRecord.kec_triple, biological_object/process/action keys). Local
    re-implementation of crossmodel_track_consistency._fmt_triple's formatting
    (not imported — that module pulls in the embedder and match machinery this
    viz has no other use for) so the two reports render a triple identically."""
    if not t:
        return ""
    names = [(t.get(k) or {}).get("name")
             for k in ("biological_object", "biological_process", "biological_action")]
    if not any(names):
        return ""
    return " / ".join(n or "—" for n in names)


def _assay_cells(a: dict, cross_track: dict | None = None) -> str:
    name = _e(a.get("assay_name"))
    mt = _e(a.get("method_type"))
    ep = _e(a.get("measured_endpoint"))
    di = _e(a.get("endpoint_direction"))
    tp = _e(a.get("throughput"))
    # direction margin if present
    m = ((a.get("logprobs") or {}).get("endpoint_direction") or {}).get("margin")
    margin = f' <span style="color:#888">m={m:.2f}</span>' if isinstance(m, (int, float)) else ""
    iris = [t.get("iri") for t in (a.get("ontology_terms") or []) if t.get("iri")]
    iri_html = (f'<div class="iri">{_e(", ".join(i.rsplit("/", 1)[-1] for i in iris))}</div>'
                if iris else "")
    ev = a.get("evidence") or []
    ev_html = (f'<div class="ev">“{_e(" … ".join(ev)[:240])}”</div>' if ev else "")

    # This model's own read of the biology this assay measures (kec_triple), and
    # whether it agrees with this model's own KEC-track triple for the same Key
    # Event (D5, response_plan.md R1 §5.3) — a coherence check needing no curated
    # reference and no second model. Omitted entirely when the run has no matching
    # track-consistency data, so the report is unchanged for runs without it.
    kec_html = ""
    kec_str = _fmt_kec_triple(a.get("kec_triple"))
    if kec_str:
        if cross_track is not None:
            matched = cross_track.get("matched")
            color = "#2e7d32" if matched else "#b71c1c"
            tag = ("agrees" if matched else "disagrees") if matched is not None else "unscored"
            kec_html = (f'<div class="kec" style="color:{color}">{_e(kec_str)} '
                        f'<span style="color:#888;font-weight:400">({tag} w/ KE)</span></div>')
        else:
            kec_html = f'<div class="kec" style="color:#555">{_e(kec_str)}</div>'

    return (f'<td>{name}{iri_html}{kec_html}</td><td>{mt}</td>'
            f'<td>{ep}</td><td>{di}{margin}</td><td>{tp}{ev_html}</td>')


def _cluster_block(cluster: dict, n_models: int, min_sup: int, ke_id: str,
                   track_consistency: dict) -> str:
    support = cluster.get("support", 0)
    rep = cluster.get("assay") or {}
    members = cluster.get("members") or []
    model_list = ", ".join(_lab(m) for m in (cluster.get("models") or []))

    member_rows = "".join(
        f'<tr><td style="font-size:11px;color:#555">{_e(_lab(m.get("model")))}</td>'
        f'{_assay_cells(m.get("assay") or {}, track_consistency.get((m.get("model"), ke_id, (m.get("assay") or {}).get("assay_name"))))}</tr>'
        for m in members)

    return f"""
<details>
<summary>{_support_bar(support, n_models, min_sup)}
  <span style="font-weight:600">{_e(rep.get("assay_name"))}</span>
  <span class="sep">|</span>
  <span style="color:#444;font-weight:400">{_e(rep.get("measured_endpoint"))}
    ({_e(rep.get("method_type"))})</span>
  <span class="sep">|</span>
  <span style="font-size:11px;color:#888">{_e(model_list)}</span>
</summary>
<table>
<thead><tr><th>model</th><th>assay</th><th>method</th><th>endpoint</th>
  <th>direction</th><th>throughput / evidence</th></tr></thead>
<tbody>
<tr class="rep"><td><b>repr.</b></td>{_assay_cells(rep)}</tr>
{member_rows}
</tbody></table>
</details>"""


if enabled:
    OUT_HTML = Path(product["html"])
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)

    with open(upstream["crossmodel_assays"]["consensus"], encoding="utf-8") as f:
        consensus = json.load(f)

    # UUID → 'KEnnn' so display keys match the KEC pipeline (older assay runs are
    # UUID-keyed; the parse_pydantic aop.json sits beside the per-model dirs).
    # Newer assay runs already key by 'KEnnn' directly — wiki_map.get(ke_id, ke_id)
    # is then a no-op passthrough for such keys.
    wiki_map = {}
    try:
        wiki_map = load_wiki_id_map(Path(products) / "aop.json")
    except Exception as ex:
        logger.warning(f"could not load wiki_id map: {ex}")

    summ = {}
    try:
        summ = json.loads(Path(upstream["crossmodel_assays"]["summary"]
                               ).read_text(encoding="utf-8")).get("summary", {})
    except Exception:
        pass

    n_models = len(summ.get("models_present") or model_keys)
    min_sup = summ.get("min_support", 2)          # clustering-report threshold
    gate = int(assay_min_support) if assay_min_support else 1  # Solr publish gate
    track_consistency = _load_track_consistency(track_consistency_json)
    if track_consistency:
        logger.info(f"track consistency: {len(track_consistency)} (model, KE, assay) rows")
    n_indexed = sum(1 for kcs in consensus.values()
                    for c in kcs if c.get("support", 0) >= gate)
    hist = summ.get("support_histogram") or {}
    info = (f"{n_models} models | {summ.get('n_clusters', 0)} clusters, "
            f"{n_indexed} to be indexed (support≥{gate}) | "
            f"clustered at support≥{min_sup} | τ={summ.get('tau')}")
    hist_html = " ".join(
        f'<span style="background:#e3f2fd;padding:1px 5px;border-radius:3px;'
        f'font-size:11px">sup={k}: {v}</span>'
        for k, v in sorted(hist.items(), key=lambda x: int(x[0])))

    def _ke_sort(item):
        return -max((c.get("support", 0) for c in item[1]), default=0)

    blocks = []
    data_kes = []
    for ke_id, clusters in sorted(consensus.items(), key=_ke_sort):
        if not clusters:
            continue
        ordered = sorted(clusters, key=lambda c: c.get("support", 0), reverse=True)
        n_idx = sum(1 for c in clusters if c.get("support", 0) >= gate)
        ke_label = wiki_map.get(ke_id, ke_id)
        inner = "".join(_cluster_block(c, n_models, gate, ke_label, track_consistency)
                        for c in ordered)
        ke_url = f"https://aop.adma.ai/?fieldId={_e(ke_label)}"
        blocks.append(f"""
<details>
<summary><a href="{ke_url}" target="_blank" rel="noopener"
  style="font-family:monospace;color:inherit;text-decoration:none;border-bottom:1px dotted"
  onclick="event.stopPropagation()">{_e(ke_label)}</a>
  <span class="sep">|</span>
  <span style="color:#888;font-size:12px">{len(clusters)} assays, {n_idx} indexed</span>
</summary>{inner}</details>""")
        # data sidecar: same per-KE grouping the HTML renders from, keyed by the
        # resolved KEnnn wiki-id so downstream consumers (assay_consensus2solr) don't
        # need to redo the uuid->wiki-id translation.
        data_kes.append({
            "ke_id": ke_label,
            "clusters": [
                {
                    "support": c.get("support", 0),
                    "models": c.get("models") or [],
                    "assay": c.get("assay") or {},
                    "members": c.get("members") or [],
                }
                for c in ordered
            ],
        })

    html_out = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Assay consensus — {_e(products)}</title><style>{_CSS}</style></head>
<body><h1>Cross-model assay consensus — {_e(products)}</h1>
<p class="info">{_e(info)}</p>
<p class="hist">Support distribution: {hist_html}</p>
{"".join(blocks)}
</body></html>"""
    OUT_HTML.write_text(html_out, encoding="utf-8")

    data_out = {
        "products": products,
        "summary": {
            "n_models": n_models,
            "models": summ.get("models_present") or model_keys,
            "n_clusters": summ.get("n_clusters", 0),
            "n_consensus": summ.get("n_consensus_clusters", 0),
            "min_support": min_sup,             # clustering-report threshold
            "assay_min_support": gate,          # Solr publish gate this run used
            "tau": summ.get("tau"),
        },
        "kes": data_kes,
    }
    if "data" in product:
        OUT_DATA = Path(product["data"])
        OUT_DATA.parent.mkdir(parents=True, exist_ok=True)
        OUT_DATA.write_text(json.dumps(data_out, ensure_ascii=False), encoding="utf-8")
        logger.info(f"wrote {OUT_HTML} + {OUT_DATA} ({len(consensus)} KEs)")
    else:
        logger.info(f"wrote {OUT_HTML} ({len(consensus)} KEs) — no 'data' product configured")
else:
    logger.info("viz_assay_consensus disabled — skipping")
