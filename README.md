# aopmapper-curation

LLM-based curation pipeline for [AOP-Wiki](https://aopwiki.org) Key Events (KEC) and
assay/measurement-methodology records (Methods2AOP), feeding
[AOPMapper](https://aopwiki.org/) as a third-party tool. This repo contains the
extraction, evaluation, consensus and model-selection pipeline described in the
accompanying paper (citation and DOI added on publication).

It is a focused extract of a larger internal pipeline, covering exactly the steps the
paper reports: it does not include the Solr-publish stage that serves curated results
into the AOPMapper index, or unrelated tooling (engineered-nanomaterial-specific
extensions, knowledge-graph-embedding experiments) that live alongside it internally.

## Setup

Requires Python 3.11–3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

### LLM endpoint

Extraction calls an OpenAI-compatible chat completions endpoint (self-hosted vLLM,
Ollama, or a hosted gateway such as OpenRouter). Point each model entry in your
`env.*.yaml` at it via `base_url`, or set a process-wide default:

```bash
export AOP4ENM_LLM_BASE_URL="http://localhost:8000/v1"
```

If a model entry sets `api_key`, that value is the *name* of an environment variable
to read the key from at runtime (e.g. `api_key: "OPEN_ROUTER_API_KEY"`) — never a
literal secret in the config file.

### AOP-Wiki data

The pipeline reads AOP-Wiki's quarterly release XML export, which is third-party data
and not redistributed here. Download it from [aopwiki.org](https://aopwiki.org) and
place it under `resources/aopwiki/`, matching the `xml_file_path` in your `env.*.yaml`
(see `env.example.yaml`).

## Reproducing the paper's steps

Each step corresponds to a Ploomber pipeline definition, run with:

```bash
uv run ploomber build -e <pipeline.yaml> [--env--<key> <value> ...]
```

| Paper step | Pipeline | Notes |
|---|---|---|
| Step 1 — KEC extraction (per model) | `pipeline.kec.yaml` + `env.kec.yaml` | `--env--model_key <key>` selects the model from the `models:` registry |
| Step 2 — cross-model KEC consensus, SMAA/TOPSIS ranking, ensemble selection | `pipeline.consensus.yaml` + `env.consensus.yaml` | reads every model's `kec.json` under `{{products}}` |
| Step 3 — assay (Methods2AOP) extraction, then cross-model consensus | `pipeline.assays.yaml` + `env.assays.yaml`, then `pipeline.assays_consensus.yaml` + `env.assays_consensus.yaml` | mirrors the KEC track |
| Step 4 — AOP 451 case study + manuscript figures/tables | `pipeline.manuscript.yaml` + `env.manuscript.yaml` (copy from `env.manuscript.yaml.example`) | exports into an external manuscript repo you supply via `manuscript_repo:`; the case-study check itself (`aop451_reference_check`) runs against `resources/aop451_reference.json`, transcribed from Nymark et al. (2021, PMID 35295099) |

`env.kec.yaml`'s `models:` registry lists the exact ~60-model panel used in the paper.
Most entries assume a private inference backend and will need your own `base_url`
before they can run — they are included for reproducibility/citation of the exact
panel, not as an out-of-the-box model catalogue.

The assay-track manuscript export's `assay_technique_stats_json` stat (distinct assay
techniques after name consolidation) is produced by the Solr-publish stage, which is
out of scope for this repo — leave it blank in `env.manuscript.yaml` if you have not
run that stage separately; the export simply skips that one statistic.

## External services

- **Ontology grounding**: `tasks/ground_kec.py` calls IDEAconsult's public dictionary
  API (`https://api.ideaconsult.net/nanoreg1/dictionary`).
- **IRI validation**: `tasks/validate_kec_iris.py` / `validate_assay_iris.py` call the
  [EBI Ontology Lookup Service (OLS4)](https://www.ebi.ac.uk/ols4/).

Both are public HTTP APIs; no credentials required.

## Testing

```bash
uv run pytest
```

## License

[GNU Affero General Public License v3.0](LICENSE), Copyright (C) IDEAconsult.

The AGPL's network-use clause applies: if you run a modified version of this pipeline
as a network service, you must make your modified source available to its users.

## Citation

Citation details and DOI (Zenodo archive of the tagged release corresponding to the
paper) will be added here on publication.
