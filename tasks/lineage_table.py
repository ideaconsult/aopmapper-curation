"""
lineage_table.py - Supplementary Table S2: triple-set Jaccard of same-checkpoint model pairs.

Import-safe library used by tasks/export_manuscript_results.py. Renders the `lineage_jaccard`
block that task builds from crossmodel_pairwise.tsv (pairs sorted by Jaccard, plus the
panel-wide distribution) as a Markdown pipe table with a pandoc caption. Pair labels and
relations come from the `lineage_pairs` config entries (`label`, `relation`), not from here.
"""
from __future__ import annotations

from pathlib import Path

HEADER = ["Pair", "Relation", "Shared", "Union", "Jaccard"]
ALIGN = ["---", "---", "---:", "---:", "---:"]

CAPTION = (
    "Table: Supplementary Table S2. Same-checkpoint model pairs of the 72-model pilot panel - "
    "a base model against its own quantization or fine-tune, or two quantizations of one base "
    "- by pairwise Jaccard similarity of their extracted triple sets (Shared / Union), against "
    "the distribution over all panel pairs."
)


def _pair_label(pair: dict) -> str:
    """The configured display label; model keys joined by "vs." when none was configured."""
    return pair.get("label") or f"{pair['a']} vs. {pair['b']}"


def render_lineage_table(lineage: dict) -> str:
    """Markdown fragment: pipe table (rows by Jaccard, descending), the all-pairs row computed
    from the distribution, a blank line, then the caption."""
    pairs = sorted(lineage["pairs"], key=lambda r: -r["jaccard"])
    dist = lineage["distribution"]
    lines = [
        "| " + " | ".join(HEADER) + " |",
        "|" + "|".join(ALIGN) + "|",
    ]
    for p in pairs:
        lines.append(f"| {_pair_label(p)} | {p.get('relation', '')} | {p['shared']} | "
                     f"{p['union']} | {p['jaccard']:.2f} |")
    lines.append(f"| *All panel pairs ({dist['n_pairs']:,})* | | | | "
                 f"*median {dist['median']:.2f}, 90th pct. {dist['p90']:.2f}, "
                 f"max {dist['max']:.2f}* |")
    return "\n".join(lines) + "\n\n" + CAPTION + "\n"


def write_lineage_table(lineage: dict, md_path: Path) -> int:
    """Write the fragment. Returns the number of pair rows written."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_lineage_table(lineage), encoding="utf-8", newline="\n")
    return len(lineage["pairs"])
