"""
assay_models.py — shared Methods2AOP assay-record schema. LIBRARY, not a task.

`AssayRecord` is the per-assay metadata shape tasks/extract_assays.py extracts from a
Key Event's measurement_methodology text. It is imported here (rather than defined
inline in extract_assays.py) so tasks/assay_consensus2solr.py can build Solr documents
against the exact same field set, without re-declaring field names by hand.

Import-safe (no module-level side effects), unlike the Ploomber task scripts —
extract_assays.py dereferences injected params at import time, so its own definitions
cannot be imported. Same pattern as tasks/kec_schema.py.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from tasks.kec_schema import AnnotatedTerm, KECTriple


class AssayRecord(BaseModel):
    """Methods2AOP-aligned metadata for a SINGLE assay."""
    assay_name: str = Field(
        description="Short normalised lab name, e.g. DAPI, 8OHG, MTT, ELISA, western blot")
    description: str = Field(
        default="", description="Concise description of what this assay measures and how")
    method_type: str = Field(
        default="not_specified",
        description="in_vitro | in_vivo | in_silico | ex_vivo | not_specified")
    cell_types: List[str] = Field(
        default_factory=list,
        description="Cell lines or primary cell types used WITH THIS assay")
    organ_tissue: List[str] = Field(
        default_factory=list, description="Organs or tissues relevant to THIS assay")
    species: List[str] = Field(
        default_factory=list, description="Species applicability for THIS assay")
    measured_endpoint: str = Field(
        default="", description="What this assay quantifies, e.g. cell count, DNA strand breaks")
    endpoint_direction: str = Field(
        default="not_specified", description="increase | decrease | change | not_specified")
    ontology_terms: List[AnnotatedTerm] = Field(
        default_factory=list,
        description="Ontology terms specific to this assay's endpoint. Max 5.")
    guideline: Optional[str] = Field(
        default=None,
        description="Formal guideline for this assay (e.g. OECD TG 487); null if none")
    throughput: str = Field(
        default="not_specified", description="high | medium | low | not_specified")
    evidence: List[str] = Field(
        default_factory=list,
        description="VERBATIM phrase(s) copied from the source text that justify this "
                    "assay and its fields. Quote, do not paraphrase. 1-3 short spans.")
    # Last field on purpose: the SpanAligner cursor in attach_assay_logprobs is
    # forward-only, so this must be emitted after the LOGPROB_FIELDS spans.
    kec_triple: Optional[KECTriple] = Field(
        default=None,
        description="The single coherent biological change THIS assay measures, in the "
                    "same (object, process, action) shape as a Key Event KEC triple.")

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data):
        """Models often emit null instead of [] / "" for empty fields — coerce so a
        single stray null doesn't fail the whole KE."""
        if not isinstance(data, dict):
            return data
        for k in ("cell_types", "organ_tissue", "species", "ontology_terms", "evidence"):
            v = data.get(k)
            if not isinstance(v, list):
                data[k] = []
        for k, dflt in (("assay_name", ""), ("description", ""),
                        ("method_type", "not_specified"), ("measured_endpoint", ""),
                        ("endpoint_direction", "not_specified"),
                        ("throughput", "not_specified")):
            if k in data and data[k] is None:
                data[k] = dflt
        return data


class AssayList(BaseModel):
    """LLM response: list of per-assay records extracted from one KE text."""
    assays: List[AssayRecord]
