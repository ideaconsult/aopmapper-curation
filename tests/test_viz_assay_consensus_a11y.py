"""viz_assay_consensus.html: no interactive element inside any <summary> (devtools a11y
warning), summaries stay plain inline content, and each KE summary shows the KE title."""
import json
import re

from scripts.check_summary_a11y import scan
from tasks.viz_assay_render import _cluster_block, ke_block, load_ke_titles

_ASSAY = {"assay_name": "HPLC-EC <b>", "method_type": "in_vitro",
          "measured_endpoint": "ACh levels", "endpoint_direction": "decrease",
          "throughput": "medium", "evidence": ["span"]}


def _page(title="Decreased, <Acetylcholine> & co"):
    cluster = {"support": 2, "models": ["a--m1", "b--m2"], "assay": _ASSAY,
               "members": [{"model": "a--m1", "assay": _ASSAY},
                           {"model": "b--m2", "assay": _ASSAY}]}
    inner = _cluster_block(cluster, 2, 2, "KE846", {})
    return ke_block("KE846", 1, 1, inner, title)


def test_no_interactive_or_wrapper_inside_any_summary():
    r = scan(_page())
    assert r["summaries"] == 2          # the KE summary + one cluster summary
    assert r["interactive"] == []
    assert r["wrappers"] == []


def test_scan_detects_a_link_in_a_summary():
    bad = '<details><summary><a href="x">KE1</a></summary></details>'
    assert scan(bad)["interactive"] == ["a"]
    assert scan('<summary><button>x</button><input></summary>')["interactive"] == [
        "button", "input"]
    assert scan('<summary><div>x</div></summary>')["wrappers"] == ["div"]
    assert scan('<a href="x">ok</a><summary>t</summary>')["interactive"] == []


def test_link_moved_into_details_body_before_clusters():
    html = _page()
    m = re.search(r'</summary>\s*<div class="ke-link"><a href="([^"]+)" target="_blank" '
                  r'rel="noopener">Open KE846 in AOPMapper</a></div>\s*<details>', html)
    assert m and m.group(1) == "https://aop.adma.ai/?fieldId=KE846"


def test_title_is_inline_escaped_text_in_summary():
    html = _page()
    ke_summary = re.search(r"<summary>(.*?)</summary>", html, re.S).group(1)
    assert ("KE846</span> - Decreased, &lt;Acetylcholine&gt; &amp; co" in ke_summary)
    assert "<Acetylcholine>" not in html
    assert "<" not in re.sub(r"<span[^>]*>|</span>", "", ke_summary)  # only spans in it


def test_missing_title_is_omitted():
    for t in (None, ""):
        ke_summary = re.search(r"<summary>(.*?)</summary>", _page(t), re.S).group(1)
        assert " - " not in ke_summary.split('<span class="sep">')[0]
        assert "KE846</span>\n" in ke_summary or "KE846</span><" in ke_summary


def _kec(tmp_path, mk, recs):
    d = tmp_path / mk
    d.mkdir()
    (d / "kec.json").write_text(json.dumps(recs), encoding="utf-8")


def test_load_ke_titles_falls_back_to_next_model_and_counts_missing(tmp_path):
    _kec(tmp_path, "m1", {"KE1": {"title": "One"}, "KE2": {"title": ""},
                          "KE3": {"ke_id": "KE3"}})
    _kec(tmp_path, "m3", {"KE2": {"title": "Two via m3"}, "KE1": {"title": "other"}})
    titles, missing = load_ke_titles(tmp_path, ["m1", "m2_absent", "m3"],
                                     ["KE1", "KE2", "KE3", "KE9"])
    assert titles == {"KE1": "One", "KE2": "Two via m3"}
    assert missing == ["KE3", "KE9"]


def test_load_ke_titles_without_root_returns_all_missing():
    assert load_ke_titles(None, ["m"], ["KE1"]) == ({}, ["KE1"])


# ── loading banner + expand responsiveness (static page chrome) ──────────────────
def _full_page():
    from tasks.viz_assay_render import page_shell
    return page_shell("T", "H", _page())


def test_loading_banner_is_first_in_body_with_status_role():
    html = _full_page()
    body = html.split("<body>", 1)[1]
    assert body.startswith('<div id="loading" role="status"')
    assert "Loading assay consensus" in body.split("</div>", 1)[0]


def test_script_removing_banner_is_at_end_of_body():
    html = _full_page()
    tail = html.rsplit("</script>", 1)[1]
    assert tail.strip() == "</body></html>"
    script = html[html.rindex("<script>"):]
    assert 'getElementById("loading")' in script and ".remove()" in script
    # delegated: exactly one click and one capturing toggle listener on document
    assert script.count("document.addEventListener") == 2
    assert '"toggle"' in script and script.count("requestAnimationFrame") >= 3
    assert "}, true);" in script           # toggle does not bubble -> capture


def test_content_visibility_rule_and_summary_css_unchanged():
    from tasks.viz_assay_render import _CSS
    assert re.search(r"details details, details table \{ content-visibility: auto;"
                     r" contain-intrinsic-size:", _CSS)
    assert "html.busy" in _CSS and "cursor: progress" in _CSS
    assert ("details > summary { cursor: pointer; padding: 6px 10px; background: #f5f5f5;\n"
            "    font-weight: 600; list-style: none; display: flex; gap: 8px; "
            "align-items: center; }") in _CSS
    rules = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)       # comments mention summary
    assert not re.search(r"summary[^{]*\{[^}]*content-visibility", rules)


def test_full_page_summaries_still_clean():
    r = scan(_full_page())
    assert r["interactive"] == [] and r["wrappers"] == []


def test_opening_chip_and_deferred_open_handler():
    html = _full_page()
    chip = '<div id="opening" role="status" aria-live="polite" hidden></div>'
    # chip is outside every <details>/<summary>, after the content, before the script
    assert html.index(chip) > html.rindex("</details>")
    assert html.index(chip) < html.index("<script>")
    script = html[html.rindex("<script>"):]
    assert "e.preventDefault()" in script and "if (d.open) return;" in script  # closing = default
    assert "if (pending) return;" in script                                    # double-click guard
    assert "d.open = true" in script and "root.classList.add(\"busy\")" in script
    assert script.index("e.preventDefault()") < script.index("d.open = true")
    assert "e.target !== pending" in script and "chip.hidden = true" in script
    assert script.count("document.addEventListener") == 2
    assert "#opening[hidden]" in _css()
    r = scan(html)
    assert r["interactive"] == [] and r["wrappers"] == []


def _css():
    from tasks.viz_assay_render import _CSS
    return _CSS
