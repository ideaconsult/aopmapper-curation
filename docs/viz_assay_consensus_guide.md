# Reading the assay consensus visualisation (`viz_assay_consensus.html`)

## What it is

`viz_assay_consensus.html` is a self-contained interactive report produced by
`tasks/viz_assay_consensus.py` after the cross-model **assay** pipeline
(`pipeline.assays_consensus.yaml`) runs. For each Key Event (KE) it shows the
**consensus assay clusters** — groups of equivalent assays that several models
independently extracted from the KE's free-text `measurement_methodology` — with
each model's structured metadata, the verbatim **evidence** it cited, ontology
links, and the endpoint-direction confidence margin.

Open in any browser — no server needed.

> **Key difference from the KEC report.** Assays have **no curated ground truth**
> in AOP-Wiki (we are introducing the structured representation). So there is no
> confirmed / missed / novel axis as in `viz_consensus.html`. Instead, clusters
> are ranked purely by **support** (how many models agree), and trust is read from
> support + evidence grounding + IRI validity. See
> [assay_quality_assessment.md](assay_quality_assessment.md) for the rationale.

---

## Page structure

```
Header bar
  "N models | M clusters, K consensus (support≥2) | τ=0.80"

Support distribution chips
  sup=1: 412   sup=2: 159   sup=3: 78   …

[KE block]  ▸  one collapsible per KE
[KE block]
…
```

KEs are labelled by their AOP-Wiki id (`KEnnn`) and sorted by **maximum cluster
support** (the KEs with the strongest cross-model agreement appear first).

---

## KE block

```
▶  KE846  |  4 assays, 3 consensus
```

- **N assays** — total consensus clusters for this KE (one per distinct assay).
- **K consensus** — how many of them reach `min_support` (default 2), i.e. were
  produced by at least that many models.

Clusters inside a KE are sorted by support, highest first.

---

## Inside a KE block — the cluster

Each assay cluster is a collapsible row:

```
████░░  4/6   HPLC-Fluorescence  |  porphyrin levels (in_vitro)  |  bggpt, gemma-4-12B, …
   model      | assay                | method   | endpoint        | direction      | throughput / evidence
   repr.      | HPLC-Fluorescence    | in_vitro | porphyrin lev.  | increase m=3.3 | medium  "…by HPLC…"
   bggpt      | HPLC-FLD             | in_vitro | porphyrin conc. | increase m=3.3 | medium  "…HPLC-FLD…"
   gemma-4-12B| HPLC-Fluorescence    | ex_vivo  | porphyrin patt. | change   m=4.4 | medium  "…fluorescence…"
   …
```

**Cluster summary line:**
- **Support bar** — `support/N_models`, colour-coded dark green ≥ 75 %, olive
  ≥ 50 %, amber ≥ 33 %, orange < 33 %. Higher = more models independently
  produced an equivalent assay = stronger ground-truth-free corroboration.
- **Representative assay name** — the cluster's canonical assay (chosen by the
  matcher), then its `measured_endpoint (method_type)`, then the list of
  contributing models.

**Member table** — one row per model's assay, preceded by the `repr.` row
(the representative). Columns:

| Column | Field | Notes |
|---|---|---|
| **model** | — | which model produced this member |
| **assay** | `assay_name` | with resolved ontology IRI fragments beneath it (e.g. `GO_0006915`) if any |
| **method** | `method_type` | controlled vocabulary (in_vitro / in_vivo / ex_vivo / in_silico / …) |
| **endpoint** | `measured_endpoint` | what is measured |
| **direction** | `endpoint_direction` | with the **`m=` margin** — the top-logprobs gap on the direction token (a classification-confidence signal; larger = more decisive) |
| **throughput / evidence** | `throughput` + `evidence` | the verbatim source span(s) the model cited, in quotes |

The **evidence** spans are the faithfulness anchor: they should be literal
substrings of the KE's `measurement_methodology` (verified separately by
`assay_evidence_check.py`). A confident-looking assay with no evidence — or
evidence not found in the source — is a hallucination flag.

---

## Are any assays dropped?

No. Every assay extracted by every model is assigned to exactly one cluster:
`assay_compare.cluster_ke_assays` places each assay in the best-matching existing
cluster or starts a new singleton (support = 1). The `min_support` threshold only
affects the **consensus counter** (`K consensus`); all clusters — including
singletons — appear in the report and in `crossmodel_consensus.json`.

The support chips at the top show how many clusters fall in each support bucket;
`sup=1` is the singleton total (assays no other model corroborated).

---

## What to look for

| Pattern | Interpretation |
|---|---|
| High-support cluster (≥ 75 %) with consistent fields | Strong, corroborated assay; high-confidence catalog entry |
| High support on `assay_name` but **disagreeing** `method_type` / `direction` | The assay identity is agreed, but its classification is not — confident-but-divergent fields need a curator (the case logprobs alone cannot resolve) |
| Cluster with **no evidence** spans | Possible hallucination — not grounded in the source text |
| Member with low `m=` direction margin | Model is unsure of the direction; treat that field as soft |
| Singleton (support = 1) | One model only; low corroboration, manual check |
| Many `not_specified` fields across the cluster | The source methodology genuinely under-specifies that facet (e.g. throughput) — not a model error |

---

## Relationship to other reports

| File | What it shows |
|---|---|
| `viz_assay_consensus.html` | **This file.** Per-KE consensus assay clusters with evidence, IRIs, margins |
| `figures/fig_assay_landscape.png` | The consensus catalog as a whole: method/direction/throughput facets, top endpoints, ontology grounding |
| `figures/fig_assay_reliability_<KE>.png` | Single-KE worked example: per-field token-logprob confidence across models |
| `figures/fig_assay_pairwise.png` | Bi-clustered model × model Jaccard agreement over clustered assays |
| `figures/fig_assay_consensus_support.png` | Distribution of cluster support counts |
| `crossmodel/assay_model_summary_all.csv` | Per-model GT-free criteria (consensus, evidence-grounded, IRI-valid, direction-margin AUC) |
| `crossmodel/smaa_criteria.{tsv,md}` | SMAA / TOPSIS model ranking over those criteria |

---

## Technical notes

**Cluster formation (the assay signature).** Two assays are merged iff their
`assay_name` matches (IRI exact → normalised-name exact → SapBERT cosine ≥ τ,
default **0.80**) **AND** (`measured_endpoint` matches at any tier **OR**
`method_type` is exactly equal). `method_type` is the exact-only "action-like"
slot. Merging is greedy and deterministic (`assay_compare.cluster_ke_assays`).

**KE labels.** Per-KE keys are mapped from AOP-Wiki UUID to `KEnnn` via
`aop_select.load_wiki_id_map` (reading `aop.json` beside the per-model dirs), so
display ids match the KEC pipeline even for older UUID-keyed assay runs.

**Sort order.** KEs are sorted by maximum cluster support (strongest agreement
first); within a KE, clusters are sorted by support, highest first.

**No ground-truth join.** Unlike `viz_consensus.py`, there is no
`*_match_report.json` to load — there is no curated assay set to match against.
Trust comes from support (this report), evidence grounding and IRI validity
(per-model checks), and the cross-model consensus criteria fed to SMAA.
