"""Did slimming viz_assay_consensus.json change anything the report shows or a task reads?

Compares an old (full) and a new (slim) viz_assay_consensus.json field by field: the
header (`products`, `summary`), and for every KE, cluster (same order) and member (same
order) the support, models, model, assay_idx and every assay field EXCEPT `logprobs`, which
is compared only through the endpoint-direction margin the report prints. Nothing is
tolerated: values, types and list order must be identical. Exits 1 and prints the first
mismatches otherwise.

With --html-old/--html-new it also checks that the two reports are byte-identical and that
neither refers to a `.json` file name that differs.

Usage:
    uv run python scripts/compare_viz_assay_json.py OLD.json NEW.json \
        [--html-old OLD.html --html-new NEW.html]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAX_REPORT = 20


def _margin(assay: dict):
    return ((assay.get("logprobs") or {}).get("endpoint_direction") or {}).get("margin")


def compare_assay(old: dict, new: dict, where: str, out: list) -> int:
    """Compare two assay dicts; returns the number of fields checked."""
    old, new = old or {}, new or {}
    keys = (set(old) | set(new)) - {"logprobs"}
    for k in sorted(keys):
        if old.get(k, "<absent>") != new.get(k, "<absent>"):
            out.append(f"{where}.{k}: {old.get(k, '<absent>')!r} != {new.get(k, '<absent>')!r}")
    if _margin(old) != _margin(new):
        out.append(f"{where}.logprobs.endpoint_direction.margin: "
                   f"{_margin(old)!r} != {_margin(new)!r}")
    return len(keys) + 1


def compare(old: dict, new: dict) -> tuple[list[str], int]:
    """(mismatch messages, number of values compared)."""
    out: list[str] = []
    n = 0
    for k in ("products", "summary"):
        n += 1
        if old.get(k) != new.get(k):
            out.append(f"header {k} differs")
    ok, nk = old.get("kes", []), new.get("kes", [])
    if [k.get("ke_id") for k in ok] != [k.get("ke_id") for k in nk]:
        out.append("KE list / order differs")
        return out, n
    for ko, kn in zip(ok, nk):
        ke = ko.get("ke_id")
        co, cn = ko.get("clusters") or [], kn.get("clusters") or []
        if len(co) != len(cn):
            out.append(f"{ke}: {len(co)} clusters != {len(cn)}")
            continue
        for i, (a, b) in enumerate(zip(co, cn)):
            w = f"{ke}.cluster[{i}]"
            for f in ("support", "models"):
                n += 1
                if a.get(f) != b.get(f):
                    out.append(f"{w}.{f} differs")
            n += compare_assay(a.get("assay"), b.get("assay"), f"{w}.assay", out)
            ma, mb = a.get("members") or [], b.get("members") or []
            if len(ma) != len(mb):
                out.append(f"{w}: {len(ma)} members != {len(mb)}")
                continue
            for j, (x, y) in enumerate(zip(ma, mb)):
                for f in sorted((set(x) | set(y)) - {"assay"}):
                    n += 1
                    if x.get(f, "<absent>") != y.get(f, "<absent>"):
                        out.append(f"{w}.member[{j}].{f} differs")
                n += compare_assay(x.get("assay"), y.get("assay"),
                                   f"{w}.member[{j}].assay", out)
    return out, n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("old_json")
    ap.add_argument("new_json")
    ap.add_argument("--html-old")
    ap.add_argument("--html-new")
    args = ap.parse_args(argv)

    old = json.loads(Path(args.old_json).read_text(encoding="utf-8"))
    new = json.loads(Path(args.new_json).read_text(encoding="utf-8"))
    problems, n = compare(old, new)
    so, sn = Path(args.old_json).stat().st_size, Path(args.new_json).stat().st_size
    print(f"json: {so} -> {sn} bytes ({sn / so:.1%}); {n} values compared; "
          f"{len(problems)} mismatches")

    if args.html_old and args.html_new:
        ho, hn = Path(args.html_old).read_bytes(), Path(args.html_new).read_bytes()
        same = ho == hn
        print(f"html: byte-identical: {same} ({len(ho)} vs {len(hn)} bytes)")
        if not same:
            problems.append("html differs")
        refs = {m for h in (ho, hn) for m in re.findall(rb"[\w.\-/]*\.json", h)}
        print(f"html .json references: {sorted(r.decode() for r in refs) or 'none'}")

    for p in problems[:MAX_REPORT]:
        print("MISMATCH", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
