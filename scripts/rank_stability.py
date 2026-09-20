"""Which models rank at the top regardless of how the criteria are chosen?

The KEC ranking is reported under three criteria sets (`default`, `iri_valid`,
`no_logprob`) precisely so that the ordering can be shown not to be an artefact of which
criteria were included. This script reads the exported manuscript stats and answers the
question that spread is there to answer: which models are near the top under *every* set,
and which owe their position to one criterion.

It reads only `manuscript/generated/*_stats.json` (written by
`tasks/export_manuscript_results.py`), so it reports what the manuscript would report and
computes no statistic of its own beyond ranking the exported first-rank probabilities.

Usage:
    uv run python scripts/rank_stability.py [path/to/manuscript/generated]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_GENERATED = Path(
    "../../../Ideaconsult/publications/_ONGOING/2026_AOPMAPPER/manuscript-repo"
    "/manuscript/generated"
)


def short(model: str) -> str:
    """Drop the org prefix and the quantization/serving suffixes that make the full
    repository identifiers unreadable in a table."""
    name = model.split("--", 1)[-1]
    for suffix in ("-NVFP4", "-vllm", "-FP8-dynamic"):
        name = name.replace(suffix, "")
    return name


#: substring -> family label, tested in order against the lowercased model id. A family is
#: a shared base lineage, not a shared checkpoint: every Gemma derivative (including the
#: medical and diffusion variants and the community requantizations) counts as one family,
#: because the question is whether the SELECTION keeps reaching for that lineage even when
#: it swaps which flavour of it.
FAMILIES = [
    ("gemma", "Gemma"),           # gemma-3/4, Gemma4-*-PrismaQuant, medgemma, diffusiongemma
    ("qwen", "Qwen"),             # Qwen3.x + quantizations/fine-tunes (incl. Athene, below)
    ("nemotron", "Nemotron"),
    ("devstral", "Devstral"),     # Mistral-lineage but reported separately, as in the text
    ("mistral", "Mistral"),
    ("granite", "Granite"),
    ("laguna", "Laguna"),
    ("llama", "Llama"),
    ("deepseek", "DeepSeek"),
    ("olmo", "Olmo"),
    ("gpt-oss", "GPT-OSS"),
    ("ling-", "Ling"),
    ("glm", "GLM"),
]

#: models whose family is not recoverable from the name (fine-tunes renamed by their author)
FAMILY_OVERRIDES = {"athene": "Qwen"}   # Athene-V2 is a Qwen2.5-72B fine-tune


def family(model: str) -> str:
    low = model.lower()
    for key, label in FAMILY_OVERRIDES.items():
        if key in low:
            return label
    for key, label in FAMILIES:
        if key in low:
            return label
    return "other"


def load(generated: Path, name: str) -> dict | None:
    path = generated / name
    if not path.exists():
        print(f"  (missing: {path})")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def ensemble_membership(stats: dict, label: str) -> dict[str, set[str]]:
    """{f"{label}:{variant}/{mode}": set(models in that ensemble)}.

    Ensemble membership is a different and more stable statistic than first-rank
    probability: SMAA's headline P(rank 1) concentrates on one or two models and says
    nothing about the rest of the panel, whereas the selection objective has to choose a
    whole subset, so a model that is never first can still be picked every time.
    """
    out: dict[str, set[str]] = {}
    for variant, modes in (stats.get("ensemble") or {}).items():
        for mode, block in (modes or {}).items():
            if not block:
                continue
            members = block.get("recommended_models") or list(
                (block.get("per_model_contribution") or {}))
            if members:
                out[f"{label}:{variant}/{mode}"] = set(members)
    return out


def rank_table(smaa: dict) -> dict[str, dict]:
    """{criteria_set: {model: (rank, first_rank_prob)}} over the exported top_models."""
    out = {}
    for variant, block in smaa.items():
        rows = block.get("top_models") or []
        ordered = sorted(rows, key=lambda r: -(r.get("first_rank_prob") or 0.0))
        out[variant] = {
            r["model"]: (i, r.get("first_rank_prob") or 0.0)
            for i, r in enumerate(ordered, 1)
        }
    return out


def main() -> None:
    generated = Path(sys.argv[1]) if len(sys.argv) > 1 else (Path(__file__).parent / DEFAULT_GENERATED)
    generated = generated.resolve()
    print(f"reading {generated}\n")

    kec = load(generated, "kec116_stats.json")
    kec_all = load(generated, "kec_all_stats.json")
    assays = load(generated, "assays_stats.json")
    if not kec:
        sys.exit("kec116_stats.json is required")

    ranks = rank_table(kec.get("smaa") or {})
    variants = list(ranks)
    print(f"KEC116 criteria sets: {', '.join(variants)}")

    # models appearing in the exported top list of every criteria set
    common = set.intersection(*(set(r) for r in ranks.values())) if ranks else set()
    # order by worst (largest) rank across sets — a model near the top everywhere has a
    # small worst-rank, which is the stability question
    by_worst = sorted(common, key=lambda m: (max(ranks[v][m][0] for v in variants),
                                             -sum(ranks[v][m][1] for v in variants)))

    extra = {}
    if kec_all:
        extra["KEC_ALL"] = rank_table(kec_all.get("smaa") or {}).get("default", {})
    if assays:
        extra["assay"] = rank_table(assays.get("smaa") or {}).get("default", {})

    header = ["model"] + [f"{v} rank (P)" for v in variants] + [f"{k} rank (P)" for k in extra]
    print("\n" + " | ".join(header))
    print("-" * 110)
    for m in by_worst:
        cells = [short(m)]
        for v in variants:
            r, p = ranks[v][m]
            cells.append(f"{r} ({p:.3f})")
        for k, table in extra.items():
            if m in table:
                r, p = table[m]
                cells.append(f"{r} ({p:.3f})")
            else:
                cells.append("-")
        print(" | ".join(cells))

    print("\nworst-rank-across-KEC-criteria-sets (lower = more stable):")
    for m in by_worst:
        worst = max(ranks[v][m][0] for v in variants)
        spread = [ranks[v][m][0] for v in variants]
        print(f"  {worst:>2}   {short(m):<45} ranks {spread}")

    # ── ensemble-membership stability ────────────────────────────────────────
    memberships: dict[str, set[str]] = {}
    for stats, label in ((kec, "KEC116"), (kec_all, "KEC_ALL"),
                         (assays, "assay")):
        if stats:
            memberships.update(ensemble_membership(stats, label))
    if not memberships:
        return

    print(f"\n\nensemble membership across {len(memberships)} selections:")
    for name in sorted(memberships):
        print(f"  {name}  ({len(memberships[name])} models)")

    counts: dict[str, int] = {}
    for members in memberships.values():
        for m in members:
            counts[m] = counts.get(m, 0) + 1
    n = len(memberships)
    print(f"\nselected in how many of the {n}:")
    for m, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        bar = "#" * c
        flag = "  <-- every selection" if c == n else ""
        print(f"  {c:>2}/{n} {bar:<{n}} {short(m):<45}{flag}")

    # ── the same count at FAMILY level ───────────────────────────────────────
    # A family can be present in every selection while no single checkpoint of it is,
    # if the objective keeps swapping which flavour it takes.
    fam_counts: dict[str, int] = {}
    fam_members: dict[str, set[str]] = {}
    for members in memberships.values():
        for fam in {family(m) for m in members}:
            fam_counts[fam] = fam_counts.get(fam, 0) + 1
        for m in members:
            fam_members.setdefault(family(m), set()).add(short(m))
    print(f"\nFAMILY present in how many of the {n} selections:")
    for fam, c in sorted(fam_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        best = max((counts[m] for m in counts if family(m) == fam), default=0)
        flag = "  <-- every selection" if c == n else ""
        print(f"  {c:>2}/{n}  {fam:<10} distinct checkpoints used: "
              f"{len(fam_members[fam]):>2}, best single checkpoint {best}/{n}{flag}")


if __name__ == "__main__":
    main()
