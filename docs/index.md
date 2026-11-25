# AOPMapper-curation

Trust-aware extraction of structured knowledge from AOP-Wiki free text: candidate
Key Event Component (KEC) triples and Methods2AOP-aligned assay records, each
carrying the evidence needed for a curator to judge it.

Every candidate is stored separately from the curated layer and carries a verbatim
evidence span, its ontology-identifier status, a cross-model support count, and a
calibrated confidence signal.

## Documentation

- [KEC curation workflow](aop_kec_curation_workflow.md)
- [Ensemble selection guide](ensemble_selection_guide.md)
- [Ensemble selection methods](ensemble_selection_methods.md)
- [Assay quality assessment](assay_quality_assessment.md)
- [Consensus visualisation](viz_consensus_guide.md)
- [Assay consensus visualisation](viz_assay_consensus_guide.md)

## Pipelines

One [Ploomber](https://ploomber.io) pipeline per stage: `pipeline.kec.yaml`
(extraction, run once per model), `pipeline.consensus.yaml` (cross-model consensus,
SMAA and TOPSIS ranking, ensemble selection and its cross-validation),
`pipeline.assays.yaml` and `pipeline.assays_consensus.yaml` (assay track),
`pipeline.manuscript.yaml` (case study and export of reported statistics), and
`pipeline.wikidiff.yaml
