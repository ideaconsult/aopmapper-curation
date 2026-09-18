Use uv. Don't use VIRTUAL_ENV — `uv sync` manages the environment, `uv run <cmd>` executes within it.

This is the public, focused extract of the AOP-Wiki KEC/assay curation pipeline (see README.md).
It does not include the Solr-publish stage or unrelated tooling out of scope for the paper.

Pipelines are Ploomber definitions, run as:

```bash
uv run ploomber build -e <pipeline.yaml> [--env--<key> <value> ...]
```

Steps map to the paper as: `pipeline.kec.yaml` (KEC extraction) →
`pipeline.consensus.yaml` (cross-model consensus, SMAA/TOPSIS, ensemble selection) →
`pipeline.assays.yaml` + `pipeline.assays_consensus.yaml` (assay track, mirrors KEC) →
`pipeline.manuscript.yaml` (AOP 451 case study + manuscript figures/tables/stats export).

Task scripts live under `tasks/`. Tests: `uv run pytest`.
