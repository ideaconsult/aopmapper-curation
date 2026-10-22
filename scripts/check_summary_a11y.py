"""Count interactive elements (<a>, <button>, <input>, <select>, <textarea>) nested inside
<summary> elements of an HTML report - browser devtools flags each one ("Interactive
element inside of a <summary> element"). Also counts <summary>s in total and
the ones with a block/flex child wrapper (a known Chromium problem: the disclosure
widget stops working).

Usage:
    uv run python scripts/check_summary_a11y.py REPORT.html
Exits 1 if any violation is found.
"""
from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path

INTERACTIVE = {"a", "button", "input", "select", "textarea"}
WRAPPERS = {"div", "p", "ul", "ol", "table", "section", "details"}
VOID = {"input", "br", "img", "hr", "meta", "link"}


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0            # >0 while inside a <summary>
        self.n_summary = 0
        self.interactive = []     # tag names found inside a summary
        self.wrappers = []

    def handle_starttag(self, tag, attrs):
        if tag == "summary":
            self.n_summary += 1
            self.depth += 1
        elif self.depth:
            if tag in INTERACTIVE:
                self.interactive.append(tag)
            if tag in WRAPPERS:
                self.wrappers.append(tag)

    def handle_endtag(self, tag):
        if tag == "summary" and self.depth:
            self.depth -= 1


def scan(html_text: str) -> dict:
    """{'summaries': n, 'interactive': [tags inside summaries], 'wrappers': [...]}."""
    p = _Scan()
    p.feed(html_text)
    p.close()
    return {"summaries": p.n_summary, "interactive": p.interactive,
            "wrappers": p.wrappers}


def main(argv=None) -> int:
    path = Path((argv or sys.argv[1:])[0])
    r = scan(path.read_text(encoding="utf-8"))
    print(f"summaries: {r['summaries']}; interactive inside summary: "
          f"{len(r['interactive'])}; block wrappers inside summary: {len(r['wrappers'])}")
    return 1 if r["interactive"] or r["wrappers"] else 0


if __name__ == "__main__":
    sys.exit(main())
