"""
viz_assay_render.py — pure HTML rendering helpers for tasks/viz_assay_consensus.py.

Split out of the Ploomber task so the markup can be unit-tested without a pipeline run
(the task file executes at import). Accessibility rule enforced here and by
tests/test_viz_assay_consensus_a11y.py: a <summary> holds only non-interactive inline
content (no <a>/<button>/<input>), and stays at its default display with inline spans
directly inside it. Links live in the <details> body.
"""
from __future__ import annotations

import html as _html

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
.ke-link { font-size: 11px; padding: 4px 10px; }
.ke-link a { color: #1565c0; }
/* Expand speed: off-screen cluster blocks / tables are not laid out until scrolled
   near (the <summary> and its display are deliberately untouched). */
details details, details table { content-visibility: auto; contain-intrinsic-size: auto 36px; }
/* click feedback (set by the inline script below) */
html.busy, html.busy * { cursor: progress !important; }
#loading, #opening { position: fixed; top: 0; left: 0; right: 0; z-index: 9;
    padding: 6px 12px; background: #fff8e1; color: #5d4037;
    border-bottom: 1px solid #ffe082; }
#opening[hidden] { display: none; }
"""

_LOADING_BANNER = ('<div id="loading" role="status" aria-live="polite">'
                   'Loading assay consensus (about 25 MB)…</div>')

_OPENING_CHIP = '<div id="opening" role="status" aria-live="polite" hidden></div>'

# Tiny, dependency-free, offline. Opening a closed KE <details> is deferred: the click
# default is cancelled, the "Opening KEnnn…" chip + busy cursor are shown, and two
# animation frames later (so they have been painted) details.open is set, which does the
# heavy layout. Closing keeps the default behaviour. One delegated click listener (KE
# summaries only, works for mouse and Enter/Space) and one capturing toggle listener
# (toggle does not bubble); the chip/cursor are cleared two frames after the toggle.
_SCRIPT = """<script>
(function () {
  var root = document.documentElement;
  var chip = document.getElementById("opening");
  var pending = null;
  function frames2(fn) { requestAnimationFrame(function () { requestAnimationFrame(fn); }); }
  document.addEventListener("click", function (e) {
    var s = e.target.closest && e.target.closest("summary");
    if (!s || s.parentNode.parentNode !== document.body) return;
    var d = s.parentNode;
    if (d.open) return;
    e.preventDefault();
    if (pending) return;
    pending = d;
    chip.textContent = "Opening " + (s.firstElementChild ? s.firstElementChild.textContent : "") + "…";
    chip.hidden = false;
    root.classList.add("busy");
    frames2(function () { d.open = true; });
  });
  document.addEventListener("toggle", function (e) {
    if (e.target !== pending) return;
    frames2(function () { pending = null; chip.hidden = true; root.classList.remove("busy"); });
  }, true);
  var b = document.getElementById("loading");
  if (b) requestAnimationFrame(function () { b.remove(); });
})();
</script>"""


def page_shell(title_html: str, h1_html: str, body_html: str) -> str:
    """Whole page: loading banner first in <body>, content, then the inline script that
    removes it once the parser has reached the end. Without JS the banner is hidden by
    the <noscript> rule rather than left up forever."""
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">\n'
            f'<title>{title_html}</title><style>{_CSS}</style>'
            f'<noscript><style>#loading{{display:none}}</style></noscript></head>\n'
            f'<body>{_LOADING_BANNER}<h1>{h1_html}</h1>\n{body_html}\n'
            f'{_OPENING_CHIP}\n{_SCRIPT}\n</body></html>')


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


def load_ke_titles(kec_products, model_keys, ke_ids) -> tuple[dict, list]:
    """KE title lookup from the per-model KEC-track kec.json files
    ({kec_products}/{model_key}/kec.json, {KEnnn: {"ke_id", "title", ...}}).

    Reads the first model that has the file; later models are opened only while some of
    `ke_ids` still lack a title. Returns (titles, missing): titles maps ke_id -> title
    for every KE found with a non-empty title; missing is the sorted list of the
    requested ids with no title in any model (rendered without one, counted by the
    caller)."""
    import json
    from pathlib import Path

    want = set(ke_ids)
    titles: dict = {}
    if kec_products:
        for mk in model_keys or []:
            if want <= set(titles):
                break
            p = Path(kec_products) / mk / "kec.json"
            if not p.exists():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            for kid in want - set(titles):
                t = ((data.get(kid) or {}).get("title") or "").strip()
                if t:
                    titles[kid] = t
    return titles, sorted(want - set(titles))


def ke_block(ke_label: str, n_clusters: int, n_indexed: int, inner: str,
             title: str | None = None) -> str:
    """One Key Event <details>. The <summary> carries the KE label as a plain <span>
    and the KE title as plain inline text right after it (no link: an <a> inside
    <summary> is an interactive-in-summary a11y violation); the AOPMapper link is the
    first element of the body. A missing title is simply omitted."""
    ke_url = f"https://aop.adma.ai/?fieldId={_e(ke_label)}"
    title_html = f" - {_e(title)}" if title else ""
    return f"""
<details>
<summary><span style="font-family:monospace;border-bottom:1px dotted">{_e(ke_label)}</span>{title_html}
  <span class="sep">|</span>
  <span style="color:#888;font-size:12px">{n_clusters} assays, {n_indexed} indexed</span>
</summary>
<div class="ke-link"><a href="{ke_url}" target="_blank" rel="noopener">Open {_e(ke_label)} in AOPMapper</a></div>{inner}</details>"""
