"""
kec_schema.py — shared KEC triple schema. LIBRARY, not a task.

The (object, process, action) shapes an extractor emits so its triples can be fed to
tasks/kec_compare.py unchanged: `normalise_triple` looks up the `biological_*` field
names and `match_slot` reads `name` / `iri`, so those names are load-bearing.

Import-safe (no module-level side effects), unlike the Ploomber task scripts —
tasks/extract_kec.py dereferences injected params at import time, so its equivalent
definitions cannot be imported.

Both extractors use it — tasks/extract_kec.py (KE description text) and
tasks/extract_assays.py (measurement methodology text) — so their triples are directly
comparable and a fix here reaches both.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator

# AOP-Wiki controlled vocabulary for the action slot.
# kec_compare.py still carries its own copy — keep the two in sync.
ALLOWED_ACTIONS = {
    "increased", "decreased", "occurrence", "disrupted", "abnormal",
    "arrested", "delayed", "premature", "pathological",
    "functional change", "morphological change",
}


class AnnotatedTerm(BaseModel):
    """A biological term with optional ontology grounding."""
    name: str = Field(description="Canonical name of the term")
    iri: Optional[str] = Field(
        default=None,
        description=("Full ontology IRI if confidently known; null if uncertain. "
                     "NEVER invent IRIs."))
    source: Optional[str] = Field(
        default=None,
        description="Ontology prefix, e.g. GO, CHEBI, PR, UBERON, CL, MO, BAO")

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_string(cls, v):
        """Accept a slot given as a plain string ("disrupted") instead of a term object.

        Models drop the wrapper for ungrounded slots — the action one especially, since
        its iri/source are always null. Coercing is much cheaper than rejecting: the
        parser validates a whole KE's assay list at once, so one bare string makes
        ChatLLMClient return {"error": ...} and the KE's ENTIRE assay list is discarded.
        """
        return {"name": v.strip()} if isinstance(v, str) else v


class KECTriple(BaseModel):
    """A single (object, process, action) triple describing one biological change."""
    biological_object: Optional[AnnotatedTerm] = Field(
        default=None,
        description=("The molecular entity, cellular structure, or organismal unit that "
                     "undergoes the change. E.g. protein, receptor, organelle, cell, "
                     "organ. null if not applicable."))
    biological_process: Optional[AnnotatedTerm] = Field(
        default=None,
        description=("The biological process or pathway that is altered. "
                     "E.g. apoptosis, oxidative phosphorylation. "
                     "null if not applicable."))
    biological_action: Optional[AnnotatedTerm] = Field(
        default=None,
        description=("The directionality or nature of the change, from ALLOWED_ACTIONS. "
                     "null if not applicable."))
