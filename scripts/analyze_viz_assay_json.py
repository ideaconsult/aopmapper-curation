"""Where do the bytes of viz_assay_consensus.json go?

The assay-consensus data sidecar is read by the publishing / export tasks and is far
larger than anything it shows: this prints a byte budget so a slimming change can be
argued from numbers rather than guessed.

Reports, for one JSON file:
  * file size vs. the same data re-serialised compactly (= whitespace overhead)
  * bytes per field path (list indices collapsed to `[]`), largest first, with the share
    of the file; this is where per-member blobs such as `logprobs` show up
  * total bytes spent on key names (the `"assay_name":` part of every member)
  * duplicated strings: how many bytes the 2nd..nth copies of every string of at least
    `--min-len` characters take (description / evidence repeated across members)
  * how much of each cluster's representative `assay` is a verbatim copy of one of its
    members' `assay`

Usage:
    uv run python scripts/analyze_viz_assay_json.py PATH/TO/viz_assay_consensus.json [--top 40]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def _blen(s: str) -> int:
    return len(s.encode("utf-8"))


def path_bytes(node, path: str, acc: Counter, keys: Counter) -> int:
    """Exact compact-serialised size of `node` in UTF-8 bytes; adds the size of every
    subtree to acc[path] (list indices collapsed to `[]`) and of every dict key (quotes and
    colon included) to keys[key]. Container overhead (braces, commas) is part of the
    container's own size."""
    if isinstance(node, dict):
        size = 2 + max(len(node) - 1, 0)
        for k, v in node.items():
            kb = _blen(_dumps(k)) + 1
            keys[k] += kb
            size += kb + path_bytes(v, f"{path}.{k}", acc, keys)
    elif isinstance(node, list):
        size = 2 + max(len(node) - 1, 0)
        for v in node:
            size += path_bytes(v, f"{path}[]", acc, keys)
    else:
        size = _blen(_dumps(node))
    acc[path] += size
    return size


def string_duplicates(node, counts: Counter, min_len: int) -> None:
    if isinstance(node, dict):
        for v in node.values():
            string_duplicates(v, counts, min_len)
    elif isinstance(node, list):
        for v in node:
            string_duplicates(v, counts, min_len)
    elif isinstance(node, str) and len(node) >= min_len:
        counts[node] += 1


def representative_copies(viz: dict) -> tuple[int, int, int]:
    """(clusters, clusters whose representative equals a member's assay, bytes of those
    representatives)."""
    n = same = nbytes = 0
    for ke in viz.get("kes", []):
        for c in ke.get("clusters", []):
            n += 1
            rep = c.get("assay")
            if any(m.get("assay") == rep for m in c.get("members") or []):
                same += 1
                nbytes += _blen(_dumps(rep))
    return n, same, nbytes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("json_path")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--min-len", type=int, default=40)
    args = ap.parse_args(argv)

    p = Path(args.json_path)
    raw = p.stat().st_size
    viz = json.loads(p.read_text(encoding="utf-8"))
    compact = _blen(_dumps(viz))
    print(f"file: {raw} bytes ({raw / 2**20:.1f} MiB); compact re-dump: {compact} bytes; "
          f"whitespace overhead: {raw - compact} bytes")

    acc: Counter = Counter()
    keys: Counter = Counter()
    total = path_bytes(viz, "$", acc, keys)
    print(f"\nlargest field paths (of {total} bytes):")
    for path, b in acc.most_common(args.top):
        print(f"  {b:>12}  {100 * b / total:5.1f}%  {path}")
    kb = sum(keys.values())
    print(f"\nkey-name bytes (all keys, quotes+colon): {kb} ({100 * kb / total:.1f}%)")
    for k, b in keys.most_common(12):
        print(f"  {b:>12}  {k}")

    dup: Counter = Counter()
    string_duplicates(viz, dup, args.min_len)
    extra = sum((n - 1) * _blen(_dumps(s)) for s, n in dup.items() if n > 1)
    uniq_rep = sum(1 for n in dup.values() if n > 1)
    print(f"\nstrings >= {args.min_len} chars: {len(dup)} distinct; {uniq_rep} occur more than "
          f"once; bytes of 2nd..nth copies: {extra} ({100 * extra / total:.1f}%)")

    n, same, rb = representative_copies(viz)
    print(f"\nclusters: {n}; representative identical to a member's assay: {same}; "
          f"bytes of those representatives: {rb} ({100 * rb / total:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
