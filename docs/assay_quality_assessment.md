# Assessing assay-extraction quality without a ground truth

*Companion to the KEC assessment docs
([aop_kec_model_selection_smaa.md](aop_kec_model_selection_smaa.md),
[discussion_model_size_and_smaa_rank.md](discussion_model_size_and_smaa_rank.md)).
This note covers the **assay (Methods2AOP) extraction** track, which differs from KEC in
one decisive way: there is no curated reference to score against.*

## The problem: no ground truth

For Key Event Components (KECs) the AOP-Wiki XML already contains *structured* biological
triples, so each model's LLM output can be scored against a curated reference
(precision = `match_rate`, recall = `gt_coverage`). **Assays have no such structured
field.** The `measurement_methodology` text describes one or more assays in free prose; we
are *introducing* the structured representation ourselves (per the Methods2AOP schema in
`resources/methods2aop_schema_proposal.xsd`). There is nothing curated to compare to.

So every GT-dependent KEC metric is replaced by a **ground-truth-free proxy**. The result
is a quality assessment built entirely from internal evidence and cross-model agreement.

## The four criteria

| KEC (has GT) | Assay (no GT) | what it measures |
|---|---|---|
| `match_rate`, `gt_coverage` | **`consensus_agreement`** | correctness proxy: fraction of a model's assays that ≥ `min_support` models independently produced an equivalent of |
| *(GT faithfulness)* | **`evidence_grounded_frac`** | the verbatim `evidence` spans the model cited are actually present in the source methodology text (judge-free faithfulness) |
| `method_iri_frac` | **`iri_valid_frac`** | the assay's `ontology_terms` IRIs resolve in OLS4 |
| `action_margin_auc` | **`direction_margin_auc`** | does the model's confidence in the `endpoint_direction` token predict consensus-correctness |

All four are higher-is-better and feed SMAA / TOPSIS exactly as the KEC criteria do
(`pipeline.assays_consensus.yaml`).

### Consensus as the correctness proxy

Per KE, equivalent assays are clustered **across models** with the hybrid matcher
(`assay_compare.cluster_ke_assays`: `assay_name` matches via IRI / name / SapBERT
embedding **AND** `measured_endpoint` matches OR `method_type` is exact). A cluster's
*support* = number of models that independently extracted an equivalent assay. An assay in
a support ≥ `min_support` cluster is treated as "corroborated"; `consensus_agreement` is
the fraction of a model's assays that are corroborated. This is the same primitive used for
the GT-free KEC consensus, applied to an assay signature.

### Evidence grounding — the faithfulness check

Each extracted assay must carry an `evidence` list: verbatim phrase(s) copied from the
source. `assay_evidence_check.py` tests whether those spans are genuinely present in the
cleaned `measurement_methodology` (normalised substring / high token-overlap). A model that
invents an assay not supported by the text scores low here **even if other models invent
the same thing**. This is the one signal that does not depend on the panel.

### Log-probabilities and AUC — yes, as in KEC

The logprob machinery transfers directly. For the controlled-vocabulary field
`endpoint_direction` we capture the top-logprobs **margin** (rank1 − rank2) — the direct
analogue of the KEC action margin — and report a **sign-checked** ROC-AUC of that margin
predicting correctness (`direction_margin_auc`; `assay_logprob_calibration.py`). Other
features (`name_logprob_mean`, `method_type_margin`, `mean_field_logprob`) are computed for
diagnostics, but the direction margin is the headline trust signal.

**The one real difference is the label.** KEC's AUC asks "does this triple match curated
GT?"; the assay AUC asks **"is this assay in a support ≥ `min_support` consensus cluster?"**
— consensus membership stands in for the missing GT label.

## An honest caveat: partial circularity

Because there is no GT, **`consensus_agreement` is both a criterion and the label that
`direction_margin_auc` is trained against.** Those two criteria are therefore not fully
independent: a model that tracks the panel scores well on both. With GT this is clean
(independent label); without it, consensus does double duty.

The **independent** signals are `evidence_grounded_frac` and `iri_valid_frac` — they are
computed against the *source text* and an *external ontology service*, not the panel. They
are what catch the dangerous failure mode the consensus criteria cannot: a model that is
**self-consistent with the panel but hallucinating** (high consensus, low evidence
grounding — an "echo chamber" of shared invention). Any assay-track conclusion should lean
on the independent signals and treat consensus as corroboration, not proof.

## Where it is reported

- per model: `{products}/assays/{model}/assay_model_summary.csv` — the four criteria plus
  context (`n_assays`, `no_evidence_frac`, best logprob feature/AUC, CARRY/WEAK/DROP
  verdict);
- combined: `crossmodel/assay_model_summary_all.csv`;
- calibration detail: per-model `assay_logprob_calibration.json` (per-feature AUC,
  point-biserial r, p-value) + `.tsv` of per-assay features;
- consensus: `crossmodel_assays/crossmodel_summary.json` and `viz_assay_consensus.html`
  (clusters by support, each showing every model's assay, its evidence spans, IRIs and
  direction margin);
- ranking: `smaa_rank.tsv`, `smaa_summary.json`, `smaa_criteria.{tsv,md}` (SMAA first-rank +
  expected rank, and deterministic TOPSIS — entropy- or preference-weighted);
- figures: `figures_assays/`.

## Visualisation

### Already produced
- **Assay landscape (`fig_assay_landscape.png`)** — *the headline assay-content figure.*
  A six-panel summary of the **consensus catalog** (support ≥ `min_support`): the
  controlled-vocabulary facets (`method_type`, `endpoint_direction`, `throughput`), the
  most frequent `measured_endpoint` values, the assays-per-KE distribution, and the
  ontology of grounded terms. It illustrates the pipeline's payoff — free-text
  `measurement_methodology` resolved into structured, ontology-linked, queryable assay
  metadata — rather than which model won. Counts are also written to `assay_landscape.tsv`.
  (E.g. on the endorsed pilot: 758 consensus assays over 115 KEs, overwhelmingly
  `in_vitro`, GO-dominated grounding; `throughput` mostly `not_specified`, a candid signal
  of what the source text supports.)
- **Pairwise model agreement (Jaccard heatmap)** — inter-model reproducibility over
  clustered assays; the assay analogue of the KEC pairwise heatmap.
- **Consensus-support histogram** — how many assays were produced by 1, 2, 3 … models;
  the mass above `min_support` is the corroborated set.
- **`viz_assay_consensus.html`** — per-KE consensus clusters with evidence and margins.

### Recommended additions (in priority order)

1. **Trust quadrant — `consensus_agreement` (x) vs `evidence_grounded_frac` (y).**
   *The single most informative assay plot.* One point per model, bubble size = `n_assays`,
   colour = model. It visually separates the four regimes the circularity caveat warns
   about:
   - top-right = trustworthy (agrees with panel **and** grounded in text);
   - bottom-right = **echo chamber** (high consensus, low grounding — shared hallucination);
   - top-left = idiosyncratic-but-faithful (grounded, but few others agree);
   - bottom-left = weak.
   This is the figure that makes the "consensus ≠ correctness" point concrete to a
   toxicology audience.

2. **Parallel-coordinates over the four criteria** — every model a line across
   `consensus_agreement`, `evidence_grounded_frac`, `iri_valid_frac`,
   `direction_margin_auc` (min–max normalised), top models highlighted. The same approach
   that replaced the crowded KEC radar; shows trade-offs (crossing lines) directly.

3. **Direction-margin ROC, per model** — overlaid ROC curves of the `endpoint_direction`
   margin predicting consensus membership, with AUC in the legend; the assay analogue of
   the KEC `fig_logprob_roc`. Communicates whether each model's confidence is calibrated.

4. **Assay-count heatmap (models × KEs)** — colour = number of assays extracted per KE,
   KEs ordered by consensus support; reveals which KEs are hard/sparse and which models
   over- or under-extract. Mirrors the KEC per-KE heatmap.

5. **Evidence-grounding tier bar** — per model, stack the fraction of assays that are
   grounded / ungrounded / have no evidence at all (`no_evidence_frac`); a quick read on
   faithfulness discipline across the panel.

Of these, **(1) the trust quadrant is the recommended headline figure** for the assay
results — it is the clearest way to show that, without a ground truth, agreement and
faithfulness must be read together.
