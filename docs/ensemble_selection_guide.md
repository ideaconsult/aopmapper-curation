# Reading the ensemble selection (`select_ensemble`)

## What it is

`tasks/select_ensemble.py` answers a different question from `smaa_rank.py`. SMAA ranks each
model on its own (one score per model); this task picks a **subset of models whose *combined*
output best covers the KEC-endorsed triples**. Since different models find different endorsed
triples, the *union* of a good subset recovers more ground truth than any single model — a
**maximum-coverage (set-cover)** problem.

It reads only data that already exists — each model's `kec_match_report.json` (the per-triple
GT match verdict) plus `kec.json` for the ground-truth triples — so no re-run of the
per-model pipeline is needed. The rationale and paper-ready text are in
[ensemble_selection_methods.md](ensemble_selection_methods.md).

## How to run

```bash
uv run ploomber task select_ensemble --entry-point pipeline.consensus.yaml
```

Configure it in `env.consensus.yaml` under the `ensemble:` block:

| key | meaning |
|---|---|
| `min_gain` | recommended cut: keep adding models while each contributes ≥ this many *new* endorsed triples (default 1) |
| `budget` | optional hard cap on subset size (`null` = no cap) |
| `smaa_link` | `none` (default) / `tiebreak` (break ties by SMAA rank) / `prefilter` (only consider top-N SMAA models) |
| `smaa_topn` | pool size when `smaa_link: prefilter` |
| `target` | `kec` (default) / `kec_plus_grounded` (also cover ontology-grounded novel consensus triples — see below) |
| `grounded_match_levels` | which grounding matches count as "grounded" (default `["aopwiki","exact"]`; add `"fuzzy"` to loosen) |
| `grounded_min_support` | a grounded-novel target needs ≥ this many grounded members (default 2 — keeps it a consensus) |

## Outputs (`products/<crossmodel>/ensemble/`)

- **`ensemble_selection.tsv`** — one row per greedy step: `step, model, new_gt` (newly
  covered endorsed triples), `cum_gt`, `cum_coverage_frac`, `pooled_precision`,
  `in_recommended`. Read top-to-bottom this *is* the coverage curve.
- **`ensemble_summary.json`** — `recommended_models`, `recommended_coverage_frac`,
  `single_best_coverage_frac` (first model alone), `max_coverage_frac` (all models), and
  `per_ke` (see below).
- **`figures/fig_ensemble_coverage.png`** — left: cumulative coverage vs. number of models,
  with the recommended cut and the best-single-model line marked; right: how many KEs the
  recommended subset covers fully / partially / not at all.

### The `per_ke` block

For each KE with ground truth, for the **recommended subset**:

```json
"KE858": {"title": "...", "n_gt": 3, "n_covered": 2, "covered_frac": 0.667,
          "covering_models": {"0": ["google--gemma-4-31B-it"], "2": ["openai--gpt-oss-120b"]},
          "missed_ref_idx": [1]}
```

`covering_models[r]` lists which models in the subset recovered endorsed triple `r`;
`missed_ref_idx` are endorsed triples no selected model found. This surfaces weakly-covered
KEs and which models are pivotal.

## Alternative target: KEC ∪ ontology-grounded novel triples

KEC endorsement is a curation snapshot, not absolute ground truth — some correct
relationships simply aren't curated yet. Set `target: kec_plus_grounded` to *also* reward
covering **novel consensus triples that are ontology-grounded**: cross-model consensus
clusters that match no endorsed triple but where ≥ `grounded_min_support` members have both
their object *and* process terms grounded (grounding match in `grounded_match_levels`). The
greedy then maximises coverage of the **union** (endorsed ∪ grounded-novel). See the "novel
≠ error" rationale in [ensemble_selection_methods.md](ensemble_selection_methods.md).

This is an **alternative objective, not a replacement**: `target: kec` is unchanged, and
even in `kec_plus_grounded` mode the endorsed-only numbers are always reported so you can
see how much of the gain is genuinely endorsed vs. merely plausible. Two things to keep in
mind: grounded targets usually far outnumber endorsed ones (so this objective favours
breadth of candidates), and term-grounding is weaker evidence than curation (two valid terms
can still form an implausible relationship) — treat grounded-novel coverage as a
curator-review pool, not a correctness score.

What changes in the outputs when `target: kec_plus_grounded`:

- **`ensemble_summary.json`** gains `target`, `n_grounded_targets`, `total_target_items`
  (= endorsed + grounded), `grounded_params`, and — for the recommended subset —
  `recommended_kec_frac` (endorsed-only) vs `recommended_grounded_frac`.
  `recommended_coverage_frac` now measures the union. (In `kec` mode these fields are still
  present: `n_grounded_targets = 0`, `recommended_kec_frac = recommended_coverage_frac`.)
- **`ensemble_selection.tsv`** gains `cum_kec` and `cum_grounded` columns alongside
  `cum_gt` (= union in this mode), so each greedy step shows the endorsed/grounded split.
- **`fig_ensemble_coverage.png`** panel A overlays two curves — the union coverage and a
  separate **KEC-endorsed-only** line — so the endorsed share is legible at every step.

## How to read it

| Observation | Interpretation |
|---|---|
| Coverage curve rises steeply then flattens | The flat part = redundant models; the elbow ≈ the recommended cut |
| `recommended_coverage_frac` ≫ `single_best_coverage_frac` | The ensemble is genuinely complementary — models cover different triples |
| `max_coverage_frac` < 1.0 | Some endorsed triples were found by *no* model (hard cases / wording gaps / KE description gaps) |
| A KE with high `missed_ref_idx` | Poorly covered even by the whole panel — candidate for prompt/curation review |
| `pooled_precision` drops as models are added | Later models add more un-endorsed triples — but note these may be **novel candidates, not errors** (see methods) |

## Relationship to other reports

| File | What it shows |
|---|---|
| `smaa_rank.tsv` / `smaa_summary.json` | Per-model ranking (best *single* model) |
| **`ensemble_summary.json`** | **This task.** Best *subset* by combined GT coverage |
| `viz_consensus.html` | Per-KE, per-GT-triple "confirmed by X/Y models" — the same underlying match data, visualised |
