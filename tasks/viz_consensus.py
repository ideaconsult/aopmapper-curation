"""
viz_consensus.py — Ploomber task: cross-model consensus triple report (data + shell).

Design mirrors viz_kec_matching.html: curated GT triples are the primary axis. Per KE:
  - each GT triple with the consensus cluster that confirms it (if any), annotated with
    support count and per-model member details;
  - GT triples not found by any model (recall misses, amber);
  - novel clusters with no GT equivalent (blue), sorted by support.

Output is split into a small fixed HTML+JS **shell** (product["html"]) that fetches a
**render-ready JSON** (product["data"], `viz_consensus_data.json`) and builds the DOM
client-side — so the served HTML stays tiny/stable while only the JSON refreshes. The
shell must be served over http (it `fetch`es the JSON); for local inspection run
`python -m http.server` in the product dir (see tasks/_viz_shell.py).

Optionally the view can be restricted to the recommended ensemble subset: set
`restrict_to_ensemble=True` and the recommended models are read from the select_ensemble
upstream (its ensemble_summary.json). Cluster members from models outside the subset are
dropped, support / consensus counts / the histogram are recomputed over the subset, and GT
triples that only non-subset models confirmed correctly fall back to "not found" — i.e. the
page shows exactly what the selected ensemble produces. Default (off) is unchanged: all
models.

The support-bar color in the rendered page reflects min_support_ensemble, the SAME
threshold tasks/build_kec_enrichment.py applies to decide whether a cluster reaches
Solr (`support < min_support_ensemble` -> dropped). It is deliberately NOT a fraction-
of-panel heatmap: with a small model panel a real, indexable cluster can have a low
support/n ratio while still clearing the actual accept bar, and coloring by ratio alone
made those look rejected. min_support (the crossmodel_compare clustering threshold) is
a different, smaller number and is shown separately in the summary line, not via color.

Ploomber:
  upstream: ["crossmodel_compare", "select_ensemble"]
  product:  {"html": shell, "data": render-ready JSON}
  params:   products, model_keys; optional restrict_to_ensemble + min_support to show
            only the recommended subset; min_support_ensemble for the accept/drop color
            (should match env.yaml's value, the actual Solr gate in build_kec_enrichment.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from tasks._viz_shell import build_shell

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["crossmodel_compare", "select_ensemble"]
product = None
enabled = True
products = "products_202606"
model_keys = []
# Restrict the view to the recommended ensemble subset (read from the select_ensemble
# upstream). Off by default → all models, exactly as before.
restrict_to_ensemble = False
min_support = 2                # consensus threshold, for recomputed counts when restricting
# accept/drop color threshold — MUST match env.yaml's min_support_ensemble, the actual
# gate tasks/build_kec_enrichment.py applies before a cluster reaches Solr.
min_support_ensemble = 1
# -

from tasks.logutil import setup_logging
logger = setup_logging(Path(product["html"]).parent / "viz_consensus.log")

# ── CSS (lives in the shell; styles classes used by the JS renderer) ───────────

_CSS = """
* { box-sizing: border-box; font-family: system-ui, sans-serif; font-size: 13px; }
body { margin: 16px; color: #222; }
h1 { font-size: 18px; margin-bottom: 4px; }
.info { font-size: 12px; color: #555; margin-bottom: 8px; }
.hist { margin-bottom: 16px; }
details { margin-bottom: 6px; border: 1px solid #ddd; border-radius: 4px; }
details > summary {
    cursor: pointer; padding: 6px 10px; background: #f5f5f5;
    font-weight: 600; list-style: none;
    display: flex; gap: 8px; align-items: center;
}
details > summary::-webkit-details-marker { display: none; }
.ke-table { width: 100%; border-collapse: collapse; }
.ke-table th {
    background: #eceff1; font-size: 11px; padding: 3px 6px;
    text-align: left; border-bottom: 2px solid #cfd8dc;
}
.ke-table td { padding: 3px 6px; vertical-align: top; border-bottom: 1px solid #f0f0f0; }
.gt-row    { background: #f1f8e9; }
.miss-row  { background: #fff8e1; }
.novel-row { background: #e3f2fd; }
.slot-name { font-weight: 500; }
.slot-iri  { font-size: 10px; color: #666; display: block; }
.logprob   { font-size: 11px; color: #888; display: block; }
.tier { display: inline-block; font-size: 10px; font-weight: 700;
        padding: 1px 5px; border-radius: 3px; white-space: nowrap; }
.sep { color: #bbb; padding: 0 2px; }
"""

# ── JS renderer (port of the former Python HTML helpers; same DOM/visuals) ─────

_RENDER_JS = r"""
function e(s){ if(s==null) return ''; return String(s).replace(/[&<>"]/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function lab(k){ return (k && k.indexOf('--')>=0) ? k.split('--').slice(1).join('--') : (k||''); }
const TIER={iri:['#1b5e20','#e8f5e9'],name:['#4e342e','#efebe9'],
  embedding:['#0d47a1','#e3f2fd'],judge:['#6a1b9a','#f3e5f5'],
  none:['#b71c1c','#ffebee'],absent:['#888','#fafafa']};
function tierBadge(t){ const c=TIER[t]||['#333','#eee'];
  return `<span class="tier" style="color:${c[0]};background:${c[1]}">${e(t)}</span>`; }
function slot(s, withLp){
  if(!s || !s.name) return '<span style="color:#bbb">—</span>';
  let iriShort = s.iri ? s.iri.split('/').pop().replace(/_/g,':') : '';
  if(iriShort.indexOf(':')<0 && s.source) iriShort = iriShort ? (s.source+':'+iriShort) : s.source;
  let out = `<span class="slot-name">${e(s.name)}</span>`;
  if(iriShort) out += `<span class="slot-iri">${e(iriShort)}</span>`;
  if(withLp && s.lp!=null){ const bar=Math.min(Math.max(Math.round((s.lp+5)/5*10),0),10);
    out += `<span class="logprob">lp=${s.lp.toFixed(3)} ${'█'.repeat(bar)}${'░'.repeat(10-bar)}</span>`; }
  return out;
}
let MIN_SUPPORT_ENSEMBLE=1;   // set from data.summary in render(); the Solr accept/drop gate
function supportBar(support,n){
  // Color reflects the SAME threshold build_kec_enrichment.py uses to gate Solr indexing
  // (support < min_support_ensemble -> dropped), not a fraction-of-panel heatmap — with a
  // small model panel, a real indexable cluster can have a low support/n ratio.
  const accepted = support>=MIN_SUPPORT_ENSEMBLE;
  const color = accepted?'#2e7d32':'#b71c1c';
  const frac=support/Math.max(n,1);
  const pct=Math.round(frac*100);
  const bar=`<span style="display:inline-block;width:70px;height:9px;background:#eee;border-radius:3px;vertical-align:middle"><span style="display:block;width:${pct}%;height:100%;background:${color};border-radius:3px"></span></span>`;
  const tag = accepted?'':' ✗ below min_support_ensemble';
  return `<span style="color:${color};font-weight:700" title="min_support_ensemble=${MIN_SUPPORT_ENSEMBLE}">${support}/${n}${tag}</span> ${bar}`;
}
function memberTable(members){
  const rows = members.map(m=>{
    const v=m.match; const tier=v?(v.method||'none'):'—';
    const score=(v&&v.score!=null)?v.score.toFixed(3):'';
    return `<tr>
      <td style="font-size:11px;color:#555;white-space:nowrap">${e(lab(m.model))}</td>
      <td>${slot(m.slots.object,true)}</td>
      <td>${slot(m.slots.process,true)}</td>
      <td>${slot(m.slots.action,true)}</td>
      <td style="font-size:11px">${tier!=='—'?tierBadge(tier):''} ${score}</td>
    </tr>`;
  }).join('');
  return `<table style="width:100%;border-collapse:collapse;margin-top:4px">
    <thead><tr style="background:#f5f5f5">
    <th style="font-size:11px;padding:2px 6px">model</th>
    <th style="font-size:11px;padding:2px 6px">object</th>
    <th style="font-size:11px;padding:2px 6px">process</th>
    <th style="font-size:11px;padding:2px 6px">action</th>
    <th style="font-size:11px;padding:2px 6px">GT match</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}
function clusterSummary(c,n,label,border){
  let bestTier='none', bestScore=null;
  for(const mem of c.members){ const v=mem.match;
    if(v && v.matched){ const t=v.method; if(bestTier==='none'||t==='iri'){bestTier=t;bestScore=v.score;} } }
  const modelLabels=c.models.map(lab).join(', ');
  const scoreS=bestScore!=null?` score=${bestScore.toFixed(3)}`:'';
  return `<details style="border-left:4px solid ${border};margin:4px 0;padding-left:6px">
    <summary style="cursor:pointer;list-style:none;display:flex;gap:8px;align-items:center;padding:3px 0;background:transparent;border:none;font-weight:normal">
      <span style="font-size:11px;color:#555">${e(label)}</span>
      ${supportBar(c.support,n)}
      <span style="font-size:11px;color:#888">${e(modelLabels)}</span>
      ${bestTier!=='none'?tierBadge(bestTier):''}
      <span style="font-size:11px;color:#888">${scoreS}</span>
    </summary>
    <table style="width:100%;border-collapse:collapse;margin:4px 0 2px 0">
    <thead><tr style="background:#eceff1">
    <th style="font-size:11px;padding:2px 6px">object (repr.)</th>
    <th style="font-size:11px;padding:2px 6px">process (repr.)</th>
    <th style="font-size:11px;padding:2px 6px">action</th>
    </tr></thead><tbody><tr>
    <td style="padding:2px 6px">${slot(c.rep.object,false)}</td>
    <td style="padding:2px 6px">${slot(c.rep.process,false)}</td>
    <td style="padding:2px 6px">${slot(c.rep.action,false)}</td>
    </tr></tbody></table>
    <details style="margin-left:8px">
    <summary style="font-size:11px;color:#888;cursor:pointer;list-style:none">${c.members.length} member triple(s) with logprobs ▸</summary>
    ${memberTable(c.members)}
    </details></details>`;
}
function keBlock(ke,n){
  const clusters=ke.clusters, gt=ke.gt;
  const clusterToGt={};
  clusters.forEach((c,ci)=>{ for(const mem of c.members){ const v=mem.match;
    if(v&&v.matched&&v.ref_idx!=null){ (clusterToGt[ci]=clusterToGt[ci]||new Set()).add(v.ref_idx); } } });
  const gtToClusters={};
  Object.keys(clusterToGt).forEach(ci=>{ clusterToGt[ci].forEach(ref=>{ (gtToClusters[ref]=gtToClusters[ref]||[]).push(+ci); }); });
  const novelIdxs=clusters.map((_,ci)=>ci).filter(ci=>!(ci in clusterToGt));
  const nGtConfirmed=Object.keys(gtToClusters).length;
  const nNovel=novelIdxs.length, nClusters=clusters.length;
  const badge = nGtConfirmed?'#2e7d32':(gt.length?'#c62828':'#555');
  let rows=[];
  gt.forEach((g,gi)=>{
    const gtCell=`<td style="padding:3px 6px">${slot(g.object,false)}</td><td style="padding:3px 6px">${slot(g.process,false)}</td><td style="padding:3px 6px">${slot(g.action,false)}</td>`;
    const confirming=gtToClusters[gi]||[];
    if(confirming.length){
      const sorted=confirming.slice().sort((a,b)=>clusters[b].support-clusters[a].support);
      const blocks=sorted.map(ci=>clusterSummary(clusters[ci],n,'confirmed by','#2e7d32')).join('');
      rows.push(`<tr class="gt-row"><td style="padding:3px 6px;font-size:11px;color:#888">GT #${gi}</td>${gtCell}<td style="padding:3px 6px"><span style="color:#2e7d32;font-size:11px">✓ confirmed (${confirming.length} cluster(s))</span>${blocks}</td></tr>`);
    } else {
      rows.push(`<tr class="miss-row"><td style="padding:3px 6px;font-size:11px;color:#888">GT #${gi}</td>${gtCell}<td style="padding:3px 6px"><span style="color:#b71c1c;font-size:11px">✗ not found by any model</span></td></tr>`);
    }
  });
  const novelSorted=novelIdxs.slice().sort((a,b)=>clusters[b].support-clusters[a].support);
  if(novelSorted.length){
    const blocks=novelSorted.map(ci=>clusterSummary(clusters[ci],n,'novel','#1565c0')).join('');
    rows.push(`<tr class="novel-row"><td style="padding:3px 6px;font-size:11px;color:#1565c0;font-style:italic" colspan="4">novel (no GT match) — ${nNovel} cluster(s)</td><td style="padding:3px 6px">${blocks}</td></tr>`);
  }
  if(!gt.length && !novelSorted.length) return '';
  let table;
  if(!gt.length){
    const blocks=clusters.slice().sort((a,b)=>b.support-a.support).map(c=>clusterSummary(c,n,'(no GT)','#1565c0')).join('');
    table=`<div>${blocks}</div>`;
  } else {
    table=`<table class="ke-table"><thead><tr><th style="width:50px">GT #</th><th>GT object</th><th>GT process</th><th>GT action</th><th>consensus</th></tr></thead><tbody>${rows.join('')}</tbody></table>`;
  }
  return `<details><summary><a href="https://aop.adma.ai/?fieldId=${encodeURIComponent(ke.ke_id)}" target="_blank" rel="noopener" style="font-family:monospace;color:inherit;text-decoration:none;border-bottom:1px dotted" onclick="event.stopPropagation()">${e(ke.ke_id)}</a><span class="sep">|</span><span style="font-weight:400;color:#444">${e((ke.title||'').slice(0,120))}</span><span class="sep">|</span><span style="color:${badge};font-size:12px">${nGtConfirmed}/${gt.length} GT confirmed</span><span style="color:#1565c0;font-size:12px">${nNovel} novel</span><span style="color:#888;font-size:12px">${nClusters} clusters</span></summary>${table}</details>`;
}
function render(data,root){
  const s=data.summary;
  const scope=s.restricted?`recommended ensemble subset — ${s.n_models} models`:`${s.n_models} models`;
  const minSup=s.min_support==null?2:s.min_support;
  MIN_SUPPORT_ENSEMBLE = s.min_support_ensemble==null?1:s.min_support_ensemble;
  // n_consensus is the will-be-indexed count (support >= min_support_ensemble), matching the
  // bar colors below; minSup (clustering floor) is reported separately as context only.
  const info=`${scope} | ${s.n_clusters} clusters total, ${s.n_consensus} to be indexed (support≥${MIN_SUPPORT_ENSEMBLE}) | clustered at support≥${minSup} | τ=${s.tau==null?'—':s.tau} | ${s.embedding||'—'}`;
  // Tint buckets by the same gate: green = these clusters get indexed, grey = dropped.
  const hist=Object.entries(s.support_histogram||{}).sort((a,b)=>(+a[0])-(+b[0]))
    .map(kv=>{ const acc=(+kv[0])>=MIN_SUPPORT_ENSEMBLE;
      const bg=acc?'#e8f5e9':'#f0f0f0', fg=acc?'#2e7d32':'#888';
      return `<span style="background:${bg};color:${fg};padding:1px 5px;border-radius:3px;font-size:11px" title="${acc?'indexed':'dropped'} (min_support_ensemble=${MIN_SUPPORT_ENSEMBLE})">sup=${kv[0]}: ${kv[1]}</span>`;
    }).join(' ');
  const models=s.models||[];
  const chips=models.map((m,i)=>`<span style="background:#eceff1;padding:1px 6px;border-radius:3px;font-size:11px;display:inline-block;margin:1px" title="${e(m)}">${i+1}. ${e(lab(m))}</span>`).join(' ');
  const modelList=`<details style="margin-bottom:12px"><summary style="cursor:pointer;font-size:12px;color:#555;list-style:none">${s.restricted?'Recommended ensemble':'Model panel'} — ${models.length} models ▸</summary><div style="margin-top:6px">${chips}</div></details>`;
  const blocks=data.kes.map(ke=>keBlock(ke,s.n_models)).filter(Boolean).join('');
  const title='Cross-model consensus triples'+(s.restricted?' (recommended subset)':'');
  document.title = 'Consensus triples'+(s.restricted?' (subset)':'')+' — ' + (data.products||'');
  root.innerHTML=`<h1>${title} — ${e(data.products)}</h1><p class="info">${e(info)}</p>${modelList}<p class="hist">Support distribution: ${hist}</p>${blocks}`;
}
"""


# ── data builder (replaces the former HTML assembly) ───────────────────────────

def _norm_slots(t: dict) -> dict:
    """Unify a triple to {object, process, action}, each {name, iri, source, lp} or None."""
    out = {}
    for s in ("object", "process", "action"):
        v = t.get(s) or t.get(f"biological_{s}")
        if v and v.get("name"):
            out[s] = {"name": v.get("name"), "iri": v.get("iri"),
                      "source": v.get("source"), "lp": v.get("logprob_mean")}
        else:
            out[s] = None
    return out


def _build_data(consensus: dict, summary_data: dict, gt_by_ke: dict,
                ke_titles: dict, match_by_model_idx: dict, products_dir: str,
                restrict: set | None = None, min_sup: int = 2,
                min_sup_ensemble: int = 1) -> dict:
    summ = summary_data.get("summary", {})
    present = summ.get("models_present") or model_keys
    if restrict is not None:
        present = [m for m in present if m in restrict]
    n_models = len(present)

    kes = []
    # support of every displayed cluster (recomputed over the subset when restricting);
    # always populated, since n_consensus is derived from it in both modes
    surviving_support = []
    for ke_id, clusters in consensus.items():
        gt = gt_by_ke.get(ke_id) or []
        cl_out = []
        for c in clusters:
            members = []
            for mem in c.get("members") or []:
                mk = mem.get("model", "")
                if restrict is not None and mk not in restrict:
                    continue                      # drop members outside the subset
                tidx = mem.get("triple_idx")
                v = (match_by_model_idx.get(mk, {}).get(ke_id, {}).get(tidx)
                     if tidx is not None else None)
                members.append({
                    "model": mk,
                    "slots": _norm_slots(mem.get("triple") or {}),
                    "match": ({"matched": bool(v.get("matched")),
                               "method": v.get("method"),
                               "score": v.get("score"),
                               "ref_idx": v.get("ref_idx")} if v else None),
                })
            if restrict is not None:
                if not members:
                    continue                      # cluster empty after subset filter
                models_list = sorted({m["model"] for m in members})
                support = len(models_list)        # recompute over the subset
            else:
                models_list = c.get("models") or []
                support = c.get("support", 0)
            surviving_support.append(support)
            cl_out.append({
                "support": support,
                "models": models_list,
                "rep": _norm_slots(c.get("triple") or {}),
                "members": members,
            })
        kes.append({
            "ke_id": ke_id,
            "title": ke_titles.get(ke_id, ke_id),
            "gt": [_norm_slots(g) for g in gt],
            "clusters": cl_out,
        })
    # same order as the former _ke_sort: most GT triples first, then ke_id
    kes.sort(key=lambda k: (-len(k["gt"]), k["ke_id"]))

    from collections import Counter
    if restrict is not None:                       # recompute panel-level counts on subset
        hist = {str(k): v for k, v in sorted(Counter(surviving_support).items())}
        n_clusters = len(surviving_support)
    else:
        hist = summ.get("support_histogram") or {}
        n_clusters = summ.get("n_clusters", 0)
    # Counted against min_support_ensemble — the gate build_kec_enrichment.py applies before
    # a cluster reaches Solr — NOT crossmodel_compare's clustering min_support. This report
    # is the manual go/no-go for publishing, so its headline count and its per-cluster bar
    # colors must both mean "will be indexed". crossmodel_compare's own precomputed
    # n_consensus_clusters is min_support-based and so is deliberately not reused, even in
    # the unrestricted branch. Clustering itself is unaffected: same clusters in, same out.
    n_consensus = sum(1 for s in surviving_support if s >= min_sup_ensemble)

    return {
        "products": products_dir,
        "summary": {
            "n_models": n_models,
            "models": present,
            "restricted": restrict is not None,
            "n_clusters": n_clusters,
            "n_consensus": n_consensus,
            "min_support": min_sup,
            "min_support_ensemble": min_sup_ensemble,
            "tau": summ.get("tau"),
            "embedding": summ.get("embedding_model"),
            "support_histogram": hist,
        },
        "kes": kes,
    }


# ── main ─────────────────────────────────────────────────────────────────────

if enabled:
    OUT_HTML = Path(product["html"])
    OUT_DATA = Path(product["data"])
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)

    with open(upstream["crossmodel_compare"]["consensus"], encoding="utf-8") as f:
        consensus = json.load(f)
    with open(upstream["crossmodel_compare"]["summary"], encoding="utf-8") as f:
        summary_data = json.load(f)

    # optional: restrict to the recommended ensemble subset (from the select_ensemble upstream)
    restrict = None
    if restrict_to_ensemble:
        es_path = Path(str(upstream["select_ensemble"]["json"]))
        if es_path.exists():
            rec = json.loads(es_path.read_text(encoding="utf-8")).get("recommended_models") or []
            if rec:
                restrict = set(rec)
                logger.info(f"restricting view to {len(restrict)} recommended ensemble models")
            else:
                logger.warning("ensemble_summary has no recommended_models — showing all models")
        else:
            logger.warning(f"restrict_to_ensemble set but {es_path} not found — showing all models")

    # load GT triples, KE titles, and per-model match reports (unchanged join)
    gt_by_ke: dict = {}
    ke_titles: dict = {}
    # match_by_model_idx[model_key][ke_id][triple_idx] = {matched, ref_idx, method, score}
    match_by_model_idx: dict = {}

    present = summary_data.get("summary", {}).get("models_present") or model_keys
    for mk in present:
        kec_path = Path(products) / mk / "kec.json"
        match_path = Path(products) / mk / "kec_match_report.json"

        if kec_path.exists():
            try:
                kec = json.loads(kec_path.read_text(encoding="utf-8"))
                for ke_id, rec in kec.items():
                    if ke_id not in gt_by_ke and rec.get("structured"):
                        gt_by_ke[ke_id] = rec["structured"]
                    if ke_id not in ke_titles and rec.get("title"):
                        ke_titles[ke_id] = rec["title"]
            except Exception as ex:
                logger.warning(f"could not load kec.json for {mk}: {ex}")

        if match_path.exists():
            try:
                report = json.loads(match_path.read_text(encoding="utf-8"))
                per_ke = report.get("per_ke") or {}
                mk_map: dict = {}
                for ke_id, matches in per_ke.items():
                    mk_map[ke_id] = {i: m for i, m in enumerate(matches)}
                match_by_model_idx[mk] = mk_map
            except Exception as ex:
                logger.warning(f"could not load kec_match_report for {mk}: {ex}")

    data = _build_data(consensus, summary_data, gt_by_ke, ke_titles,
                       match_by_model_idx, products, restrict=restrict,
                       min_sup=int(min_support),
                       min_sup_ensemble=int(min_support_ensemble))
    OUT_DATA.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    OUT_HTML.write_text(
        build_shell(title="Consensus triples", data_file=OUT_DATA.name,
                    css=_CSS, render_js=_RENDER_JS),
        encoding="utf-8")

    n_ke = len(consensus)
    n_cl = sum(len(v) for v in consensus.values())
    logger.info(f"wrote {OUT_HTML} (shell) + {OUT_DATA} ({n_ke} KEs, {n_cl} clusters)")
else:
    logger.info("viz_consensus disabled — skipping")
