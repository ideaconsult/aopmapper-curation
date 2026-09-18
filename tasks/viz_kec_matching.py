"""
viz_kec_matching.py — Ploomber task: HTML report of per-KE triple match decisions.

For each KE shows LLM triples side-by-side with their matched curated triple
(or "no match"), the tier used (IRI / name / embedding), overall score, and
per-slot logprob_mean.  Unmatched curated triples are listed separately —
those are recall misses and potential incompleteness evidence.

Ploomber:
  upstream: ["extract_kec", "compare_kec"]
  product:  {"html": per-model HTML report}
  params:   model_key, tau
"""
from __future__ import annotations

import html as _html
import json
from pathlib import Path

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["extract_kec", "compare_kec"]
product = None
enabled = True
model_key = None
tau = 0.75
# -

from tasks.logutil import setup_logging
logger = setup_logging(Path(product["html"]).parent / "viz_kec_matching.log")

# ── helpers ───────────────────────────────────────────────────────────────────

_TIER_STYLE = {
    "iri":       ("IRI",       "#1b5e20", "#e8f5e9"),
    "name":      ("name",      "#4e342e", "#efebe9"),
    "embedding": ("embed",     "#0d47a1", "#e3f2fd"),
    "judge":     ("judge",     "#6a1b9a", "#f3e5f5"),
    "absent":    ("absent",    "#888",    "#fafafa"),
    "none":      ("✗",         "#b71c1c", "#ffebee"),
}

_CSS = """
* { box-sizing: border-box; font-family: system-ui, sans-serif; font-size: 13px; }
body { margin: 16px; color: #222; }
h1 { font-size: 18px; margin-bottom: 4px; }
.summary-bar { font-size: 12px; color: #555; margin-bottom: 16px; }
details { margin-bottom: 6px; border: 1px solid #ddd; border-radius: 4px; }
summary {
    cursor: pointer; padding: 6px 10px;
    background: #f5f5f5; font-weight: 600; font-size: 13px;
    list-style: none; display: flex; gap: 8px; align-items: center;
}
summary::-webkit-details-marker { display: none; }
.ke-table { width: 100%; border-collapse: collapse; }
.ke-table th {
    background: #eceff1; font-size: 11px; font-weight: 600;
    padding: 4px 6px; text-align: left; border-bottom: 2px solid #cfd8dc;
}
.ke-table td { padding: 4px 6px; vertical-align: top; border-bottom: 1px solid #f0f0f0; }
.matched-row  { background: #f1f8e9; }
.miss-row     { background: #fff8e1; }
.unmatched-row{ background: #fce4ec; }
.slot-name { font-weight: 500; }
.slot-iri  { font-size: 10px; color: #666; display: block; }
.logprob   { font-size: 11px; color: #888; display: block; }
.tier-badge {
    display: inline-block; font-size: 10px; font-weight: 700;
    padding: 1px 5px; border-radius: 3px; white-space: nowrap;
}
.score { font-size: 11px; color: #555; }
.sep   { color: #bbb; padding: 0 2px; }
.gt-miss-header { font-size: 11px; color: #b71c1c; font-weight: 600;
                  padding: 4px 6px; background: #fce4ec; }
"""


def _e(s: str) -> str:
    return _html.escape(str(s)) if s else ""


def _slot_html(slot: dict | None, *, show_logprob: bool = False) -> str:
    if not slot or not slot.get("name"):
        return '<span style="color:#bbb">—</span>'
    name = _e(slot.get("name", ""))
    iri  = _e(slot.get("iri", ""))
    src  = _e(slot.get("source", ""))
    lp   = slot.get("logprob_mean")
    out  = f'<span class="slot-name">{name}</span>'
    if iri:
        label = f"{src}:{iri.rsplit('_', 1)[-1]}" if "_" in iri else (src or iri)
        out += f'<span class="slot-iri">{label}</span>'
    if show_logprob and lp is not None:
        bar = int(min(max((lp + 5) / 5 * 10, 0), 10))  # rough 0-10 scale
        out += f'<span class="logprob">lp={lp:.3f} {"█" * bar}{"░" * (10-bar)}</span>'
    return out


def _tier_badge(method: str) -> str:
    label, color, bg = _TIER_STYLE.get(method, ("?", "#333", "#eee"))
    return (f'<span class="tier-badge" '
            f'style="color:{color};background:{bg}">{label}</span>')


def _normalise(t: dict) -> dict:
    """unify biological_X and X slot names → {object, process, action}."""
    out = {}
    for s in ("object", "process", "action"):
        out[s] = t.get(s) or t.get(f"biological_{s}")
    return out


def _ke_block(ke_id: str, rec: dict, matches: list, ke_title: str) -> str:
    structured  = rec.get("structured") or []
    llm_triples = (rec.get("llm_extracted") or {}).get("triples") or []
    if not llm_triples and not structured:
        return ""

    n_match = sum(1 for m in matches if m.get("matched"))
    matched_gt_idxs = {m["ref_idx"] for m in matches
                       if m.get("matched") and m.get("ref_idx") is not None}

    badge_color = "#2e7d32" if n_match else "#c62828"
    badge = (f'<span style="color:{badge_color};font-size:12px">'
             f'{n_match}/{len(llm_triples)} matched</span>')
    gt_badge = (f'<span style="color:#888;font-size:12px">'
                f'{len(structured)} GT</span>')

    rows = []
    for i, triple in enumerate(llm_triples):
        norm = _normalise(triple)
        m    = matches[i] if i < len(matches) else {}
        matched = m.get("matched", False)
        ref_idx = m.get("ref_idx")
        method  = m.get("method", "none")
        score   = m.get("score")

        ref = structured[ref_idx] if (matched and ref_idx is not None
                                       and ref_idx < len(structured)) else None
        ref_norm = _normalise(ref) if ref else {}

        row_cls = "matched-row" if matched else "unmatched-row"
        score_html = (f'<span class="score">{score:.3f}</span>' if score is not None
                      else "")

        # action match indicator
        llm_act  = (norm["action"] or {}).get("name", "")
        ref_act  = (ref_norm.get("action") or {}).get("name", "") if ref else ""
        act_match = (llm_act.lower() == ref_act.lower()) if llm_act and ref_act else False
        act_icon  = "✓" if act_match else ("✗" if ref else "")

        rows.append(f"""
<tr class="{row_cls}">
  <td style="color:#888;font-size:11px">{i}</td>
  <td>{_slot_html(norm["object"],  show_logprob=True)}</td>
  <td>{_slot_html(norm["process"], show_logprob=True)}</td>
  <td>{_slot_html(norm["action"])} {f'<span style="color:#888;font-size:10px">{act_icon}</span>'}</td>
  <td style="text-align:center">{_tier_badge(method) if matched else _tier_badge("none")}
      <br>{score_html}</td>
  <td>{_slot_html(ref_norm.get("object"))  if ref else '<span style="color:#bbb">—</span>'}</td>
  <td>{_slot_html(ref_norm.get("process")) if ref else '<span style="color:#bbb">—</span>'}</td>
  <td>{_slot_html(ref_norm.get("action"))  if ref else '<span style="color:#bbb">—</span>'}</td>
</tr>""")

    # unmatched GT triples (recall misses)
    miss_rows = []
    for j, gt in enumerate(structured):
        if j in matched_gt_idxs:
            continue
        gn = _normalise(gt)
        miss_rows.append(f"""
<tr class="miss-row">
  <td colspan="4"><em style="color:#888;font-size:11px">GT only (no LLM match)</em></td>
  <td></td>
  <td>{_slot_html(gn.get("object"))}</td>
  <td>{_slot_html(gn.get("process"))}</td>
  <td>{_slot_html(gn.get("action"))}</td>
</tr>""")

    title_safe = _e(ke_title[:120])
    table = f"""
<table class="ke-table">
<thead>
  <tr>
    <th>#</th>
    <th>LLM object</th><th>LLM process</th><th>LLM action</th>
    <th>tier / score</th>
    <th>GT object</th><th>GT process</th><th>GT action</th>
  </tr>
</thead>
<tbody>
{"".join(rows)}
{"".join(miss_rows)}
</tbody>
</table>"""

    return f"""
<details>
<summary>
  <span style="font-family:monospace">{_e(ke_id)}</span>
  <span class="sep">|</span>
  <span style="font-weight:400;color:#444">{title_safe}</span>
  <span class="sep">|</span>
  {badge} {gt_badge}
</summary>
{table}
</details>"""


def _render(kec: dict, per_ke: dict, summary: dict, model: str, tau_val: float) -> str:
    mr   = summary.get("match_rate", 0) or 0
    emb  = summary.get("embedding_model") or "—"
    mc   = summary.get("method_counts", {})
    info = (f"match rate {mr*100:.1f}%  "
            f"({summary.get('n_matched', 0)}/{summary.get('n_pred_triples', 0)} triples, "
            f"{summary.get('n_ke', 0)} KEs)  |  "
            f"τ={tau_val}  |  {emb}  |  "
            f"tiers: IRI={mc.get('iri',0)} name={mc.get('name',0)} "
            f"embed={mc.get('embedding',0)}")

    blocks = []
    for ke_id, rec in sorted(kec.items()):
        if not rec.get("structured") and not (rec.get("llm_extracted") or {}).get("triples"):
            continue
        matches = per_ke.get(ke_id) or []
        title   = rec.get("title") or ke_id
        blocks.append(_ke_block(ke_id, rec, matches, title))

    body = "\n".join(b for b in blocks if b)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Triple match report — {_e(model)}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Triple match report — {_e(model)}</h1>
<p class="summary-bar">{_e(info)}</p>
{body}
</body>
</html>"""


# ── main ──────────────────────────────────────────────────────────────────────

if enabled:
    OUT_HTML = Path(product["html"])
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)

    with open(upstream["extract_kec"]["json"], encoding="utf-8") as f:
        kec = json.load(f)
    with open(upstream["compare_kec"]["json"], encoding="utf-8") as f:
        report = json.load(f)

    per_ke  = report.get("per_ke", {})
    summary = report.get("summary", {})

    html_out = _render(kec, per_ke, summary, model_key or "", tau)
    OUT_HTML.write_text(html_out, encoding="utf-8")
    logger.info(f"wrote {OUT_HTML}  ({len(kec)} KEs)")
else:
    logger.info("viz_kec_matching disabled — skipping")
