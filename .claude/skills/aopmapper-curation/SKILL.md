---
name: aopmapper-curation
description: Work in the aopmapper-curation pipeline repo - LLM-based AOP-Wiki Key Event (KEC) and assay (Methods2AOP) curation, cross-model consensus, SMAA/TOPSIS ranking, ensemble selection, and manuscript stats/figure export. Use when running or editing Ploomber pipelines, task scripts under tasks/, tests, env.*.yaml configs, or regenerating manuscript/generated stats for the AOP4ENM paper.
---

# aopmapper-curation pipeline repo

Public, focused extract of the AOP-Wiki KEC/assay curation pipeline behind the AOP4ENM
paper. Does not include the Solr-publish stage or unrelated tooling out of scope for the
paper (see `README.md`).

## Environment

Requires Python 3.11-3.12 and `uv`. Use uv for everything - `uv sync` to set up, `uv run
<cmd>` to execute. Never a manually activated venv / `VIRTUAL_ENV`.

## Data lives in a sibling repo

Extraction outputs and the AOP-Wiki release data are **not** in this repo. They're in the
`aopmapper-curation-data` repo (CC BY-SA 4.0), checked out as a sibling directory
(`../aopmapper-curation-data`), holding per-release trees (`aopwiki_20260401/`, `aopwiki_20260701/`,
e.g. `aopwiki_20260401/KEC116/crossmodel/...`) plus its own `manuscript/` subtree and
`provenance.json`. `env.*.yaml` `products:`/source paths in this repo reference it with
relative paths like `..\aopmapper-curation-data\aopwiki_20260401\KEC116\crossmodel\...` - if
a path in an `env.*.yaml` doesn't resolve, check there before assuming it's missing.

## Pipelines

Each corresponds to one paper step, run as Ploomber DAGs:

```bash
uv run ploomber build -e <pipeline.yaml> [--env--<key> <value> ...]
```

| Step | Pipeline | Notes |
|---|---|---|
| 1 - KEC extraction (per model) | `pipeline.kec.yaml` + `env.kec.yaml` | `--env--model_key <key>` selects the model from the `models:` registry |
| 2 - cross-model KEC consensus, SMAA/TOPSIS ranking, ensemble selection | `pipeline.consensus.yaml` + `env.consensus.yaml` | reads every model's `kec.json` under `{{products}}` |
| 3 - assay (Methods2AOP) extraction + consensus | `pipeline.assays.yaml` + `env.assays.yaml`, then `pipeline.assays_consensus.yaml` + `env.assays_consensus.yaml` | mirrors the KEC track |
| 4 - AOP 451 case study + manuscript export | `pipeline.manuscript.yaml` + `env.manuscript.yaml` (copy from `env.manuscript.yaml.example`) | writes into the external manuscript repo set by `manuscript_repo:` |

`env.kec.yaml`'s `models:` registry lists the exact panel used in the paper; most entries
need your own `base_url` before they'll run.

## Task scripts (`tasks/`)

Extraction: `extract_kec.py`, `extract_assays.py`, `llm_client.py`, `kec_schema.py`.
Cross-model analysis: `crossmodel_compare.py`, `crossmodel_assays.py`,
`crossmodel_track_consistency.py`, `smaa_rank.py`, `select_ensemble.py`, `compare_kec.py`,
`kec_compare.py`, `audit_kec_truth.py`. Grounding/validation: `ground_kec.py`,
`ontology_ground.py`, `validate_kec_iris.py`, `validate_assay_iris.py`,
`aopwiki_ontology_index.py`. Figures/viz: `figures_crossmodel.py`, `figures_assays.py`,
`figviz.py`, `viz_consensus.py`, `viz_assay_consensus.py`, `viz_kec_matching.py`. Manuscript
export: `export_manuscript_results.py` (see below). Reference case study:
`aop451_reference_check.py`.

## External services

- Ontology grounding: `tasks/ground_kec.py` calls IDEAconsult's public dictionary API.
- IRI validation: `tasks/validate_kec_iris.py` / `validate_assay_iris.py` call EBI OLS4.

Both public HTTP APIs, no credentials required.

## Testing

`uv run pytest` (tests under `tests/`, `pythonpath = ["."]` per `pyproject.toml`).

## Manuscript numbers rule

Every number that ends up in the AOP4ENM manuscript (a separate repo, see the
`manuscript-workflow` skill there) must be produced by `pipeline.manuscript.yaml`
(`tasks/export_manuscript_results.py` -> `manuscript/generated/*_stats.json` in the
manuscript repo) or a figure configured in `env.manuscript.yaml`. Never hand-compute a
manuscript statistic. If a needed statistic isn't exported yet: add it to
`export_manuscript_results.py`, re-run with `uv run ploomber build --partially
export_<section> --entry-point pipeline.manuscript.yaml --force`, then cite the regenerated
file. Anything still ad hoc gets a `[TODO-CODE]` marker in the manuscript naming the task
that should emit it. A stats block that comes back empty (e.g. an `if not path.exists():
return None` guard swallowing a wrong path) is a bug, not "no data" - treat it as one.

## No transient scripts

Any inspection or one-off analysis code (poking at a stats JSON, checking a config, a
throwaway comparison) goes in a real file under `scripts/` or `tests/`, following existing
conventions in this repo - never a scratchpad file or a multi-line inline `python -c` /
heredoc. This applies even when the check itself is disposable.
