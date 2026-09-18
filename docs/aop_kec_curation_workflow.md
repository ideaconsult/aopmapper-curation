# Trustworthiness-scored automated curation of AOP-Wiki: measurement methodologies and Key Event Components

*Draft workflow article for the [F1000Research AOP collection](https://f1000research.com/collections/aop/about-this-collection).*
*Status: working draft describing the **planned workflow**. Section tags: **[implemented]** = pipeline
component exists and runs; **[planned]** = designed, not yet evaluated. All quantitative values are
**preliminary, single-model, illustrative** — not final results. Final numbers await the multi-model
and cross-model evaluation described in §5.*

**Authors:** _TBD_ — confirmed coauthor: Penny Nymark; remaining authors TBD.
**Affiliations:** _TBD (IdeaConsult Ltd. and collaborators)_
**Corresponding author:** _TBD_

---

## Abstract

Adverse Outcome Pathways (AOPs) organise mechanistic toxicology into chains of Key Events (KEs).
Two kinds of KE annotation are essential for downstream use yet largely unstructured in AOP-Wiki:
the **measurement methodologies** (the assays evidencing each KE, held as free text in the
`measurement_methodology` field) and the **Key Event Components (KECs)** — *(biological object,
biological process, biological action)* triples. Both are incomplete across the knowledge base, and —
as we show for KECs — a substantial fraction of the structured entries that *do* exist are
questionable. Manual curation neither scales to the growing knowledge base nor guarantees correctness.

We present an automated, **trustworthiness-scored** curation workflow built on the open `aop4enm`
pipeline that targets *both* structures. For **assays**, a large language model (LLM) extracts
per-assay Methods2AOP metadata (method type, cell types, organ/tissue, species, endpoint, direction,
ontology terms, throughput) from `measurement_methodology`, consolidates assay names, and emits a
two-entity (assay technique ∥ KE–assay) representation for the Solr backend. For **KECs**, the LLM
extracts triples from KE descriptions while capturing token-level log-probabilities; a hybrid semantic
matcher aligns them to the curated annotations; and an LLM-as-judge audit estimates the reliability of
the curated ground truth itself.

This article describes the workflow for both targets and reports **preliminary, illustrative**
trustworthiness results for the KEC component, where curated ground truth is available. Two early
signals (single model, one AOP-Wiki release) motivate the approach. First, the model's own confidence
appears predictive of correctness: the **action classification margin** from token log-probabilities
separates correct from incorrect triples with **AUC ≈ 0.79** in this pilot. Second, an LLM judge rates
only **~46%** of curated KEC triples as fully correct and **~21% as wrong**, giving preliminary
evidence of substantial label noise in AOP-Wiki KEC curation. We deliberately frame these as pilot
measurements: robust numbers require the **multi-model extraction and cross-model consensus**
evaluation set out in §5.

Since the pilot, the workflow has been extended in three directions that are now **implemented and
running**: (i) a **multi-model campaign** (a panel of 30+ models of differing size and provenance, run
sequentially on a single accelerator, plus hosted models via OpenRouter); (ii) **model selection by
SMAA/TOPSIS** — a multi-criteria decision analysis that ranks the panel on ground-truth-free criteria
to choose which model(s) to commit to the full knowledge base; and (iii) a **ground-truth-free
trustworthiness assessment for the assay track**, mirroring the KEC machinery but replacing curated
labels with cross-model consensus, evidence-grounding faithfulness, and ontology-IRI validity (since
AOP-Wiki has no structured assay field to score against).

The structured outputs feed the Apache Solr index behind **[AOPMapper](https://aop.adma.ai)**, where
curators explore and visualise AI-extracted assays and AI-suggested KECs (with confidence) alongside
existing entries flagged as likely incorrect. The pipeline is reproducible (Ploomber, Pydantic,
unit-tested) and is intended as AI-*assisted* curation — confidence-gated, with human review where
uncertainty is high — rather than blind automation.

---

## 1. Introduction

The Adverse Outcome Pathway framework underpins non-animal (new approach methodology, NAM) toxicology
and regulatory science. [AOP-Wiki](https://aopwiki.org) is its central, community-curated repository.
Making it machine-actionable requires structuring two things that today are largely free text or
absent:

1. **Measurement methodologies (assays).** Each KE carries a free-text `measurement_methodology` field
   describing — often in a single block — the *several* assays that evidence the event. Structured,
   per-assay metadata (method type, cell types, organ/tissue, species, measured endpoint and
   direction, ontology grounding, guideline, throughput) is needed for assay search, comparison and
   reuse, but is not captured in machine-readable form.
2. **Key Event Components (KECs).** A KE should carry a structured KEC triple:
   - **biological object** — the entity that changes (protein, organelle, cell, tissue, chemical …);
   - **biological process** — the process altered (apoptosis, oxidative phosphorylation …);
   - **biological action** — the direction/nature of change, from a small controlled vocabulary
     (*increased, decreased, occurrence, disrupted, abnormal, arrested, delayed, premature,
     pathological, functional change, morphological change*).

Both structures share the same two failure modes. **Coverage:** assays are unstructured and KEC
triples are missing for more than half of KEs; manual curation cannot keep pace. **Correctness:**
where structured entries exist they are treated as ground truth, yet curation is uneven and
error-prone — a problem that has been under-recognised because there was no scalable way to measure it.

We address both with an automated pipeline that does not merely *fill* the gaps but does so with an
explicit, calibrated **trustworthiness signal**, and that simultaneously **audits the existing ground
truth**. The guiding principle is AI-assisted curation: high-confidence outputs can be fast-tracked,
while uncertain cases — and likely-wrong existing entries — are routed to human reviewers. Results are
delivered to curators through the AOPMapper web application. This article describes the workflow for
both extraction targets and reports trustworthiness results for the KEC component, where curated
ground truth supports quantitative evaluation; the assay extraction is in production and its
trustworthiness evaluation is the immediate next step.

---

## 2. Methods **[implemented]**

### 2.1 Data and parsing

AOP-Wiki is exported as XML (release **2026-04-01**). The `parse_pydantic` task converts the XML to a
typed Pydantic data model (`aopkb`, generated from the AOP-Wiki XSD via `xsdata`) and serialises it to
`aop.json`. All downstream tasks consume this canonical JSON. Two extraction tracks then run over it:
**assays** from `measurement_methodology` (§2.2) and **KECs** from KE descriptions (§2.3–2.7).

### 2.2 Assay (measurement-methodology) extraction *(overview)* (`extract_assays.py`, `assay2solr_rich.py`)

A single `measurement_methodology` field typically describes several assays. The extractor therefore
(i) identifies the distinct assays in the text, then (ii) extracts per-assay Methods2AOP metadata —
method type, cell types, organ/tissue, species, measured endpoint and direction, ontology terms,
guideline, throughput. A consolidation step normalises and groups assay names (resolving synonyms),
filters ontology IRIs by cross-KE frequency, and emits a **two-entity** representation — a reusable
*assay technique* record and per-KE *KE–assay* links — for the Solr backend. Assay deduplication uses
sentence-embedding similarity. This track is in production; its dedicated trustworthiness evaluation
(matching, confidence, audit — mirroring §3) is reported later in this same article (§5). The
following sections (§2.3–§4) develop the KEC track in detail, because curated ground truth there
enables the quantitative pilot evaluation; the same machinery then carries over to the assays.

### 2.3 Two sources of KECs

Each KE yields two kinds of KEC triple:

- **`structured`** — curated triples extracted directly from the XML `biological-events`, each
  referencing global biological-object/process/action records that carry ontology IRIs. This is the
  existing **human-curated ground truth** (present for ~50% of KEs).
- **`llm_extracted`** — triples the LLM extracts from the free-text KE description. This is the new
  content, produced for KEs regardless of whether they already have curated annotations.

Both use an identical triple schema, so they are directly comparable.

### 2.4 LLM extraction with token log-probabilities (`extract_kec.py`)

The extractor prompts an OpenAI-compatible chat model to return a list of coherent
*(object, process, action)* triples plus organ/cell terms, as structured JSON. Critically, the
extractor decomposed an earlier flat design (three independent lists) into **bound triples**, because
correctness is a property of the *combination*, not of independent slots.

The action slot is constrained to the AOP-Wiki controlled vocabulary; out-of-vocabulary actions
(e.g. "inhibited") are logged rather than silently relabelled, leaving canonical-synonym decisions to
the curators.

When the endpoint supports it, the extractor requests `logprobs` and `top_logprobs` and attaches, by
aligning response tokens back to each value span:

- per-slot `logprob_mean` / `logprob_min`;
- for the action slot, the **`top_logprobs` margin** — the log-probability gap between the chosen
  action token and the runner-up. Because the action is a controlled vocabulary, the alternatives are
  competing *classes* (e.g. *decreased* vs *increased*), so this margin is a direct classification
  confidence;
- a per-response aggregate (mean log-probability, perplexity).

Raw per-token arrays are streamed to a side-car (`kec_logprobs.jsonl`); the main output keeps only the
aggregates. Persistence is incremental and crash-safe (atomic checkpoints; resumable), so a long
multi-model campaign on a single accelerator survives interruption.

### 2.5 Hybrid triple matcher (`tasks/kec_compare.py`)

Aligning an extracted triple to a curated one cannot rely on exact ontology IDs — these almost never
coincide between model and curator (the same entity is often grounded to different IRIs). Matching is
therefore semantic and tiered, cheapest first, per slot:

1. **IRI exact** — normalised ontology IDs equal;
2. **name exact** — lowercased, punctuation-collapsed string equality (no external dependency);
3. **embedding** — cosine similarity ≥ τ (default 0.75) using a biomedical sentence embedder
   (**SapBERT**, `cambridgeltl/SapBERT-from-PubMedBERT-fulltext`, chosen for biomedical
   entity/synonym linking; encodings are cached per process);
4. **LLM judge** *(optional)* — resolves borderline embedding cases (τ−0.25 ≤ similarity < τ).

The **action slot is an exception**: it is matched by name only (never embedded), because the
controlled-vocabulary terms such as *increased* and *decreased* are close in embedding space but
opposite in meaning.

**A triple matches iff** the action matches (name exact) **AND** at least one of {object, process}
matches at any tier. Slots that are absent on both sides do not count as positive evidence.

**Within a KE**, matching runs greedy 1-to-1: for each extracted triple, the highest-scoring
unmatched reference triple is found; if it meets the match rule, the reference triple is consumed so
no curated triple is credited twice.

**Why tiering matters in practice:** exact ontology-ID matching recovers essentially zero alignments.
Adding name-exact doubles that; enabling the SapBERT embedding tier recovers roughly 79/324 triples
(≈24%) in the pilot — roughly double the lexical-only rate — by recognising synonyms such as
*programmed cell death* ↔ *apoptosis*. The threshold τ is an explicit hyperparameter; a sweep across
τ values is planned (Figure 4). The matcher is an import-safe library with unit tests (`tests/`).

### 2.6 Log-probability calibration (`tasks/kec_logprob_calibration.py`)

For every extracted triple on a KE that has curated ground truth, we label it correct/incorrect via
the matcher, then measure how well each log-probability feature predicts that label (ROC-AUC,
point-biserial correlation, reliability binning). This is a deliberate **go/no-go gate**: log-prob
features are carried into downstream modelling only if they demonstrably predict correctness.

### 2.7 Ground-truth label-noise audit (`tasks/audit_kec_truth.py`, `tasks/llm_judge.py`)

To quantify the reliability of the curated ground truth, an LLM judge reads each KE description and
rates each **curated** structured triple as *correct / partial / wrong* with a justification. The
judge model is a configurable parameter (`judge_model_key`), so it can be a different — ideally
stronger or independent — model than the extractor, reducing shared-blind-spot bias.

### 2.8 Cross-model consensus pipeline (`pipeline.consensus.yaml`) **[implemented]**

A *second* Ploomber pipeline runs over the per-model product directories once any panel of models has
been extracted. `crossmodel_compare.py` clusters each KE's triples across all models with the same
hybrid matcher (§2.5), emitting (i) a **consensus** record per cluster — the number of models that
independently produced an equivalent triple, a ground-truth-free trust signal — and (ii) **pairwise
Jaccard** agreement between every model pair. `figures_crossmodel.py` then renders the cross-model
figures: match-tier breakdown, consensus-support distribution, a **bi-clustered** model × model Jaccard
heatmap (rows/columns reordered by average-linkage hierarchical clustering on Jaccard distance), a
multi-dimension radar and scatter-matrix comparison, per-feature log-prob AUC, curated-truth verdicts,
and per-KE triple-count plots (per model, plus a cross-model heatmap). The panel is configured in
`env.consensus.yaml` (the `model_keys` list, `tau`, `min_support`).

### 2.9 Reproducibility

The workflow is a [Ploomber](https://ploomber.io) DAG (`pipeline.kec.yaml`) with per-model product
directories, so models can be swapped one at a time on a single accelerator and their outputs compared;
`pipeline.consensus.yaml` (§2.8) then compares the resulting panel. The matcher and judge logic are
covered by unit tests.

---

## 3. Preliminary results (single-model pilot) **[implemented]**

> **These are illustrative pilot numbers, not final results.** They come from a **single** local model
> (**`gemma-4-26B`**, `dgx_gemma4`) on one AOP-Wiki release (2026-04-01, `products_202606/`), with the
> *same* model used for extraction and audit. They demonstrate that the workflow produces meaningful
> signals and motivate it; reliable estimates require the multi-model and cross-model evaluation in §5,
> and an independent judge for the audit. Treat every value below as provisional.

### 3.1 Semantic matching recovers alignments that exact matching misses

Exact ontology-ID matching of extracted to curated triples yields essentially **zero** matches. The
hybrid matcher recovers **43/324** triples with IRI + exact-name tiers, rising to **79/324 (≈24%)**
once the SapBERT embedding tier is enabled — roughly doubling recall over lexical matching alone. The
modest absolute rate is itself informative: model and curator frequently disagree, which motivates the
audit in §3.3 (the disagreement is partly the curation's fault, not the model's).

### 3.2 Model confidence appears to predict triple correctness

Using matcher-derived correctness labels, per-feature discrimination in the pilot was:

| Log-probability feature | ROC-AUC | point-biserial r | p |
|---|---:|---:|---:|
| **action `top_logprobs` margin** | **0.787** | **+0.427** | 8.6 × 10⁻¹⁶ |
| triple mean log-probability | 0.661 | +0.217 | 8 × 10⁻⁵ |
| response perplexity | 0.520 | +0.046 | 0.41 (≈ chance) |

The **action classification margin is a strong, highly significant trust signal**; the response-level
perplexity is uninformative. This justifies carrying the action-margin (and, more weakly, the per-slot
mean) into the planned quality model, and dropping perplexity.

### 3.3 A fifth of curated KEC triples are judged wrong (pilot)

The label-noise audit over **109 KEs / 145 curated triples** (single judge model):

| Verdict | Triples | KEs |
|---|---:|---:|
| correct | 67 (46.2%) | 53 (48.6%) |
| partial | 48 (33.1%) | 34 (31.2%) |
| **wrong** | **30 (20.7%)** | **22 (20.2%)** |

Only ~46% of curated triples are judged fully correct and **~21% are judged wrong** — direct evidence
that human KEC curation is not uniformly reliable, and that treating it as immutable ground truth would
miscalibrate any downstream guarantee.

**Caveats.** (i) The judge here is the same model family as the extractor (though it did not author the
curated triples it grades); an independent/stronger judge is a one-parameter change and will tighten
the estimate. (ii) A share of "wrong" verdicts reflect **controlled-vocabulary friction** — the judge
objects to *abnormal* used as an action — rather than biological error, so 20.7% is an upper bound on
genuine error. Both caveats are addressable and are themselves useful signals for curators.

---

## 4. Delivery and visualisation: AOPMapper **[implemented / integration ongoing]**

The `aop4enm` pipeline builds the Apache Solr index that powers
**[AOPMapper](https://aop.adma.ai)** — a React single-page application for searching, filtering and
visualising AOP-Wiki entities (AOPs, MIEs, KEs, KERs, KECs, AOs, chemicals, stressors, assays,
taxonomy). Curators see AI-suggested KEC triples carrying their confidence scores alongside existing
curation, with likely-incorrect entries flagged for review — turning the audit and confidence signals
into an actionable curation queue rather than a static report.

The same indexed data is also reachable by AI assistants through an MCP server
(`https://mcp.aop.adma.ai/mcp`); this is currently a demonstration and will be enhanced.

---

## 5. Planned evaluation and extensions **[planned]**

The pilot in §3 is a single model on one release. The core of the study is a systematic
**multi-model, cross-model** evaluation that turns those pilot signals into robust estimates. The
extraction and cross-model **machinery is implemented** (§2.8) and a panel is being run; the bullets
below are tagged by what remains. Full specification in `aop_annotation_pipeline_spec.md`.

- **Multi-model extraction.** **[machinery implemented; campaign in progress]** Run a panel of models
  (10–20, of differing size and training distribution) through the same `extract_kec` workflow. Because
  the local accelerator hosts one model at a time, models are swapped via the `model_key` parameter and
  run sequentially, each writing to its own product directory — the pipeline's per-model layout and
  crash-safe resume are designed for exactly this campaign. Outputs: per-model matching, log-prob
  calibration and audit, so §3.1–3.3 become distributions across models rather than single points.
- **Cross-model consensus pipeline.** **[implemented]** A *separate* pipeline
  (`pipeline.consensus.yaml`, §2.8) clusters each KE's triples across all models via the same hybrid
  matcher; the number of models independently producing an equivalent triple (consensus support) is a
  **ground-truth-free trust signal** — the only one available on the unannotated half of KEs — and is
  robust to the annotated/unannotated distribution shift. It also yields model-agreement (Jaccard)
  comparisons, rendered as a bi-clustered heatmap, for the benchmark. The aggregate numbers across the
  full panel are pending campaign completion.
- **Triple-level quality model + conformal prediction.** A learned model predicting P(triple correct)
  from the log-prob, embedding-distance, IRI-validity and consensus features, wrapped in conformal
  prediction for distribution-free coverage guarantees. The annotated and unannotated KE sets are
  **not exchangeable** (KEs were curated because they belong to older, better-studied AOPs), so
  Mondrian/covariate-shift conformal is required, and the curated labels must first be de-noised using
  the §3.3 audit.
- **Assay extraction evaluation.** Extend the same trustworthiness machinery (matching, confidence,
  audit) from KECs to the assay/methods extraction.
- **Phase-2 curation tasks** — KE suggestion, splitting and merging.

---

## 6. Figure suggestions *(implementation later)*

Each figure lists its data source and whether it is backed by current results or planned.

- **Figure 1 — Workflow architecture.** AOP-Wiki XML → `aop4enm` Ploomber pipeline with two extraction
  tracks: **assays** (`extract_assays` → name consolidation → two-entity assay-technique ∥ KE–assay)
  and **KECs** (`extract_kec` [curated `structured` ∥ LLM triples + log-probs] → hybrid matcher →
  log-prob calibration + ground-truth audit → multi-model + cross-model consensus → quality model +
  conformal *(planned)*) → Solr index → AOPMapper. Schematic. *(architecture)*
- **Figure 2 — Anatomy of a KEC triple.** One worked KE: the curated triple beside the LLM triple,
  per-slot log-prob annotations, and a callout of the action `top_logprobs` margin. *(real data)*
- **Figure 3 — Log-probability trustworthiness.** (a) ROC curves for action-margin (AUC 0.79),
  triple-mean (0.66) and perplexity (0.52); (b) reliability diagram (binned confidence → empirical
  correctness); (c) inset bar chart of a single action's `top_logprobs` (*decreased* ≫ *increased*).
  *(real data)*
- **Figure 4 — Hybrid matcher.** Stacked bar of matches by tier (IRI / name / embedding) and a
  match-rate-vs-τ sweep; SapBERT vs a general embedder; the exact-ID ≈ 0 baseline annotated.
  *(real data)*
- **Figure 5 — Curation quality audit (headline).** Stacked bars of correct/partial/wrong at triple
  and KE level (46/33/21), optionally split into vocabulary-friction vs genuine error. *(real data)*
- **Figure 6 — Cross-model comparison.** Bi-clustered model × model agreement heatmap (Jaccard);
  distribution of consensus support per triple; per-model triple-accuracy vs ground truth; multi-
  dimension radar / scatter-matrix across models; per-KE triple counts (per model + cross-model
  heatmap). Figures implemented (`figures_crossmodel.py`); aggregate values pending the full panel.
  *(machinery implemented; results pending)*
- **Figure 7 — Conformal coverage–efficiency.** Empirical coverage vs fraction auto-accepted, per α and
  per stratum; resulting human-review-queue size. *(planned)*
- **Figure 8 — AOPMapper delivery.** AOPMapper UI for a KE showing scored KEC triples (curated +
  AI-suggested with confidence; likely-wrong flagged). MCP access noted in the caption only. *(UI)*

---

## 7. Data and software availability **[implemented]**

- Pipeline: `ideaconsult/aop4enm` — Ploomber tasks (`tasks/`), `pipeline.kec.yaml`, unit tests
  (`tests/`).
- AOPMapper: web application (https://aop.adma.ai) and MCP server (https://mcp.aop.adma.ai/mcp).
- Source data: AOP-Wiki XML release 2026-04-01 (https://aopwiki.org/downloads).
- Embedding model: `cambridgeltl/SapBERT-from-PubMedBERT-fulltext`. Extraction/judge model in the
  reported run: `gemma-4-26B`.
- Generated artefacts referenced above: `kec.json`, `kec_match_report.json`,
  `kec_logprob_calibration.json`, `kec_truth_audit.json` under `products_202606/<model>/`.
- Licensing: _TBD_.

## Author contributions, competing interests, grant information

_TBD._

---

*Numbers in §3 are traceable to the corresponding product JSON files and may be regenerated by
re-running `pipeline.kec.yaml`. This draft will be revised as the de-biased audit (independent judge)
and cross-model results become available.*
