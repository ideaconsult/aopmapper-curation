# Plan: XML export (+ XSLT view) of curated assays and full-corpus KEC

Repo for all edits: `aopmapper-curation` (not the stale aop4enm cwd). Use `uv`. Schemas are frozen: no semantic edit to `aop.xsd` or `methods2aop_schema_proposal.xsd`.
Phased: **Phase A assays, Phase B KEC**, each independently shippable. Reviewed adversarially once (FAIL, findings folded in below; report: `plans/plan-to-add-a-playful-pizza-agent-a6913bfd21c8b01b9.md`).

## Context
Curated assays (Methods2AOP track) and full-corpus KEC (KEC_ALL, 1,583 KEs) exist only as JSON. We want XML valid against the frozen schemas, plus XSLT so a user can read it. The two tracks differ:
- KEC: `aopkb/aop.py` (xsdata, from aop.xsd) already is the class set; `aop.json` is its dump. Load `Data` and serialize with xsdata `XmlSerializer`. No hand-written mapping of the KE structure.
- Assays: `AssayRecord` (`tasks/assay_models.py:22`) is hand-written, not generated from the proposal XSD, so a mapping to generated classes IS needed (renames: `organ_tissue`->`organ-tissues/organ-tissue`, `species`->`species/taxon`, `evidence`->`evidence/source-text`, `ontology_terms{name,iri,source}`->`ontology-term{label,iri,ontology}`; `kec_triple`, `support`, `models`, `technique_id` have no element and are not exported).

## Prerequisites (shared)
1. **Proposal XSD is not well-formed XML** (verified): `--` inside the banner comment (line 8) and a nested `<!-- -->` inside the example comment (line 272). Comment-only fix (no element/type change), approved, applied to a copy under `resources/`.
2. Add `lxml` and `xmlschema` (XSD 1.1, since both schemas declare `vc:minVersion=1.1`) via `uv add`. One validator for tests: `xmlschema`. lxml only as a cross-check.
3. Generate `aopkb/methods2aop.py` with xsdata-pydantic from the (comment-fixed) proposal XSD, separate from `aopkb/aop.py`, which is untouched. Note both schemas share the namespace `http://www.aopkb.org/aop-xml`, so assay documents must never be mixed into aop.xsd documents.
4. Short-form id -> IRI expansion via an explicit prefix map (OBO purl for GO/UBERON/CL/..., BAO uses `bao#`, EFO/CHMO/NCIT own bases); unknown prefix -> term omitted and counted in the report.

## Phase A - Assay XML
- Library `tasks/assay_xml.py` (import-safe): `AssayRecord`-level records -> `methods2aop` classes; `write_xml`, `validate`. Coerce free-string enums (`method_type`, `endpoint_direction`, `throughput`) to `not_specified` when outside the XSD enum, counted in the report. Omit empty wrappers and empty `guideline`; follow XSD sequence order.
- Source of records: do NOT re-call `clusters_to_records` with different filters. Preferred: extend `assay_consensus2solr` to also emit its filtered, label-bearing records (product `xml_records`), so the XML uses exactly the curation filters (ke_graph current KEs, OLS-valid ids, `iri_min_freq`, min support). New task `tasks/export_assays_xml.py` in `pipeline.yaml` after it, `upstream: ["assay_consensus2solr"]` reading that product.
- Root (decided: assays are a separate top-level object, not part of the KE, like `biological-process` in aop.xsd): a new small auxiliary `assays.xsd` in its own target namespace that `xs:import`s the frozen proposal namespace and reuses `measurement-method-type` unchanged. Root `<assays>` holds repeated `<assay id="GUID" ke-id="KE123" support=.. models=..>` entries (dictionary-style, GUID via uuid5 from KE id + technique/assay name), each wrapping a frozen-type method. The frozen proposal XSD itself is not edited beyond the comment repair. Open assumption: the KE link is the `ke-id` attribute (and the optional support/models attributes), since the frozen `key-event` cannot reference assays.
- Output `{{products}}/xml/assays.xml` (single document, all KEs) + `xml_report.json` (counts: coerced enums, dropped terms, unknown prefixes, validation failures). Two-pass validation: `assays.xsd` (with the imported frozen types).
- XSLT `resources/xslt/assays.xsl`: KE -> table of methods, ontology links to OLS. Browsers block XSLT PIs over file://, so also ship a task step applying it with lxml to static HTML.

## Phase B - KEC XML (full corpus, clustered)
- Source: `viz_consensus.json` (`kes[]`: `gt`, `clusters{support,models,rep,members[{model,slots,match{matched,method,score,ref_idx}}]}`, `summary`). The statuses confirmed/missed/novel are NOT in it; derive them in Python (port the JS logic at `viz_consensus.py` ~150-185: a gt triple is confirmed when an accepted cluster's member match has `matched` and that `ref_idx`; else missed; clusters matching no gt are novel). The XSLT then consumes derived status, so HTML and XML agree. Test the derivation.
- Task `tasks/export_kec_xml.py` in `pipeline.consensus.yaml`, `upstream: ["viz_consensus"]`, plus the KE UUID lookup (`aop.json` / ke_graph lookup, KE number -> wiki UUID). The AOP-Wiki KEs and their curated `biological-events` are NOT rewritten or touched; the consensus is a separate object that references KEs.
- Full-corpus config: pin a dedicated env (env.consensus.yaml defaults to KEC116) pointing `products` at the KEC_ALL release; `restrict_to_ensemble: False` (KEC_ALL reports extraction, not selection; no ensembles), acceptance threshold an explicit param.
- Output `{{products}}/.../xml/kec_consensus.xml`: a separate document type, own new XSD `kec_consensus.xsd` (aop.xsd untouched), same pattern as the assays: a top-level container of objects with UUID `id`s, deterministic uuid5 over a fixed namespace constant. Objects: consensus triples (one per cluster: `ke-id` = wiki KE UUID; object/process/action each referencing a term entry by UUID; `support`; `models`; derived `status`; curated-match `method`/`score`; per-model `members` with each model's own triple), and the term entries (name, ontology source, source-id) keyed by (object|process|action, source, normalised id; casefolded name when no iri). Plus the panel summary and support histogram. Curated triples are referenced (the KE UUID plus the matched curated triple), not duplicated. Stable ids (KE `aop-wiki-id`) only in lookup entries (`key-event-reference id=UUID aop-wiki-id=N`), as in the wiki.
- Limits, all reported in `xml_report.json`: no `action-id` constraint applies here (not `biological-event`), so action-less clusters are kept; slot logprobs stay in kec.json; KEs without a wiki UUID are listed in the report.
- XSLT `resources/xslt/kec_consensus.xsl` mirrors `viz_consensus`: curated triples with confirming cluster (support bar, models), missed (amber), novel by support (blue), panel summary + histogram; static HTML step as in Phase A.

## Verification
- `tests/test_assay_xml.py`, `tests/test_kec_xml.py`, `tests/test_kec_consensus_status.py` (flat, `tmp_path`, inline dict fixtures, import library modules only): round trip classes->XML->parse; xmlschema validation; empty-list/guideline omission; enum coercion; prefix expansion incl. BAO; status derivation incl. unmatched/ties; uuid5 determinism and slot separation; action-less cluster; restricted vs unrestricted data. `uv run pytest`.
- Run the tasks on existing products (`aopmapper-curation-data/aopwiki_20260701/KEC_ALL/...` incl. its `crossmodel/` viz json, and an existing assays product) with `uv run ploomber build -e <yaml> --partially <task>`; validate all output; open generated HTML.
- No manuscript numbers produced here; KEC wire format unchanged.

## Decisions taken (from user)
- Comment-only repair of the proposal XSD: approved.
- Assays get their own root, separate from KE: approved. Superseded: the user then asked to merge into ONE schema (`methods2aop_schema_proposal.xsd`, the supplementary file) with a top-level container; no separate `assays.xsd`.
- Ids follow AOP-Wiki: schema objects carry only a UUID `id`; stable ids (wiki numeric `aop-wiki-id`, our `KEASSAY_...` assay ids) live only in lookup entries (`*-reference id=UUID ...`). Assays reference KEs by wiki UUID.
- The merged XSD replaces `manuscript/supplementary/methods2aop_schema_proposal.xsd` in the manuscript repo (copied by `build.py` via SUPPLEMENTARY_FILES); check `note_s2_methods2aop.md` wording afterwards.
