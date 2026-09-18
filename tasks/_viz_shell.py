"""
_viz_shell.py — shared static HTML shell for the consensus visualisations. LIBRARY.

The consensus reports (viz_consensus / viz_assay_consensus) used to bake *all* data into
one large self-contained HTML string (~14 MB for KECs). They now emit a small, fixed
HTML+JS **shell** that `fetch()`es a companion `*_data.json` and renders the DOM
client-side, so the committed/served HTML stays tiny and stable while only the JSON
refreshes (and compresses well).

IMPORTANT: a fetch-based shell loads only when **served over http** (vite dev/preview, the
AOPMapper server) — NOT by double-clicking the file (`file://` blocks `fetch`). For local
inspection of the raw aop4enm output, run `python -m http.server` in the product dir and
open the page from `http://localhost:8000/...`.
"""
from __future__ import annotations

import json


def build_shell(*, title: str, data_file: str, css: str, render_js: str) -> str:
    """Return the fixed HTML shell that fetches `data_file` and calls `render(data, root)`.

    `render_js` must define a global `function render(data, root)`. The shell is
    data-independent, so it changes only when the renderer/CSS changes — not on refresh.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div id="app">Loading…</div>
<script>
const DATA_FILE = {json.dumps(data_file)};
{render_js}
fetch(DATA_FILE)
  .then(r => {{ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }})
  .then(data => render(data, document.getElementById('app')))
  .catch(err => {{
    document.getElementById('app').innerHTML =
      '<p style="color:#b71c1c;font-family:system-ui,sans-serif">Could not load <code>'
      + DATA_FILE + '</code>: ' + err + '<br>This page must be <b>served over http</b> '
      + '(e.g. <code>python -m http.server</code> in this folder), not opened as a file.</p>';
  }});
</script>
</body>
</html>"""
