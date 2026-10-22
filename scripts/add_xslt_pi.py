"""Copy an XML export and add an xml-stylesheet processing instruction after the XML declaration.

The pipeline products stay untouched; the copy is what is published (for example on GitHub Pages,
where a browser applies the stylesheet when the XML is opened over HTTP).

Usage: uv run python scripts/add_xslt_pi.py SRC.xml DST.xml HREF
"""
from __future__ import annotations

import argparse
from pathlib import Path


def add_stylesheet_pi(xml: bytes, href: str) -> bytes:
    """Insert <?xml-stylesheet?> right after the XML declaration (or at the start without one)."""
    pi = f'<?xml-stylesheet type="text/xsl" href="{href}"?>\n'.encode("utf-8")
    if b"<?xml-stylesheet" in xml[:2000]:
        raise ValueError("document already has an xml-stylesheet instruction")
    head = xml.lstrip()[:5]
    if head == b"<?xml":
        end = xml.index(b"?>") + 2
        # keep whatever line break follows the declaration
        rest = xml[end:].lstrip(b"\r\n")
        return xml[:end] + b"\n" + pi + rest
    return pi + xml


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("href")
    args = ap.parse_args()
    out = add_stylesheet_pi(args.src.read_bytes(), args.href)
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_bytes(out)
    print(f"wrote {args.dst} ({len(out)} bytes), stylesheet {args.href}")


if __name__ == "__main__":
    main()
