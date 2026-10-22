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
the HTML — so downstream consumers (e.g. a publishing stage) can consume the same
already-KEnnn-keyed, per-KE-grouped view without re-reading crossmodel_assays' raw
consensus file and redoing the wiki-id translation.

Ploomber:
  upstream: ["crossmodel_assays"]
  product:  {"html": ..., "data": ...}
  params:   products, model_keys
"""
from __future__ import annotations

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
# accept/drop color threshold; overridden by env.assays_consensus.yaml's assay_min_support,
# the publish acceptance threshold.
# Deliberately NOT the same as crossmodel's own `min_support` (the clustering-report
# threshold, shown separately in the info line): the bar previously coloured by
# support/n_models as a fixed-fraction gradient, so on an 11-model panel a cluster at
# the actual publish gate (e.g. support=3, assay_min_support=2) still rendered red
# because 3/11 is below the gradient's amber cutoff. Binary accept/drop against the
# real gate, mirroring tasks/viz_consensus.py's equivalent fix for the KEC track.
assay_min_support = 1
# Slim the JSON sidecar (opt-in; env key viz_slim_data): keep every assay field the report
# shows or a downstream task reads, but reduce each record's `logprobs` block (~60% of
# the bytes) to the endpoint-direction margin the report prints (tasks/viz_assay_slim.py).
# False (default) writes the full records. The HTML is identical either way.
slim_data = False
# KEC-track product root holding {model_key}/kec.json — the source of KE titles shown in
# each KE summary (env.assays_consensus.yaml track_consistency.kec_products). None/missing
# files just leave the titles out.
kec_products = None
# -

from tasks.aop_select import load_wiki_id_map
from tasks.logutil import setup_logging
from tasks.viz_assay_render import (_e, _cluster_block, ke_block,
                                      load_ke_titles, page_shell)
from tasks.viz_assay_slim import slim_cluster


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

    # KE titles (plain text in each KE summary) from the KEC-track kec.json files;
    # KEs with no title in any model render without one and are counted/logged.
    ke_titles, no_title = load_ke_titles(
        kec_products, model_keys,
        [wiki_map.get(k, k) for k, v in consensus.items() if v])
    logger.info(f"KE titles: {len(ke_titles)} found, {len(no_title)} missing"
                + (f" ({', '.join(no_title)})" if no_title else ""))

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
        blocks.append(ke_block(ke_label, len(clusters), n_idx, inner, ke_titles.get(ke_label)))
        # data sidecar: same per-KE grouping the HTML renders from, keyed by the
        # resolved KEnnn wiki-id so downstream consumers don't
        # need to redo the uuid->wiki-id translation.
        data_clusters = [
            {
                "support": c.get("support", 0),
                "models": c.get("models") or [],
                "assay": c.get("assay") or {},
                "members": c.get("members") or [],
            }
            for c in ordered
        ]
        if slim_data:
            data_clusters = [slim_cluster(c) for c in data_clusters]
        data_kes.append({"ke_id": ke_label, "clusters": data_clusters})

    html_out = page_shell(
        f"Assay consensus — {_e(products)}",
        f"Cross-model assay consensus — {_e(products)}",
        f"""<p class="info">{_e(info)}</p>
<p class="hist">Support distribution: {hist_html}</p>
{"".join(blocks)}""")
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
        OUT_DATA.write_text(
            json.dumps(data_out, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")
        logger.info(f"wrote {OUT_HTML} + {OUT_DATA} ({len(consensus)} KEs)")
    else:
        logger.info(f"wrote {OUT_HTML} ({len(consensus)} KEs) — no 'data' product configured")
else:
    logger.info("viz_assay_consensus disabled — skipping")
