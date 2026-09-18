"""
extract_kec.py — Extract Key Event Components (KECs) from AOP-Wiki Key Event text

Extracts biological objects, biological processes, and biological actions from
Key Event description text using an LLM, and grounds them against ontology terms.

Background
----------
AOP-Wiki Key Events have two sources of KEC information:

1. STRUCTURED (already in XML): Each KE may have biological_events entries,
   each referencing global biological_object / biological_process / biological_action
   records that already carry ontology annotations (source_id = IRI, source = prefix).
   These are the ground truth — we always output them.

2. UNSTRUCTURED (this script's job): The KE description field contains rich
   biological text that often mentions objects, processes and actions not captured
   in the structured events. The LLM reads this text and extracts additional KECs,
   suggesting ontology grounding where possible.

Output per KE
-------------
{
  "ke_id":       "KE123",
  "title":       "...",
  "structured":  [                          # from XML biological-events (ground truth)
    {
      "object":  {"name": "...", "iri": "GO:0005739", "source": "GO"},
      "process": {"name": "...", "iri": "GO:0006915", "source": "GO"},
      "action":  {"name": "...", "iri": "MO:...", "source": "MO"}
    }, ...
  ],
  "llm_extracted": {                        # from LLM on description text
    "triples": [                            # coherent (object, process, action) units
      {
        "biological_object":  {"name": "...", "iri": "...|null", "source": "...", <logprobs>},
        "biological_process": {"name": "...", "iri": "...|null", "source": "...", <logprobs>},
        "biological_action":  {"name": "...", "iri": null, "source": null, <logprobs>, "top_logprobs": [...]}
      }, ...
    ],
    "organ_terms": [{"name": "...", "iri": "...|null", "source": "...", <logprobs>}],
    "cell_terms":  [{"name": "...", "iri": "...|null", "source": "...", <logprobs>}],
    "_response_logprob": {"mean": -0.19, "perplexity": 1.21, "n_tokens": 142}
  }
}

Errors and resume
-----------------
A KE whose LLM call fails is written to kec.json with its curated `structured` triples
but `llm_extracted: null`; the reason goes to the `kec_errors.json` sidecar as
{ke_id: {"error": ..., "title": ..., "ts": ...}}. Resume treats a KE as done only when
`llm_extracted` is populated, so errored KEs (and legacy records carrying the old
inline `llm_error` field) are re-queued on the next run. A KE that succeeds on retry is
dropped from kec_errors.json, which therefore lists what is *currently* failing.

Logprobs (trustworthiness signal)
---------------------------------
When the endpoint returns token logprobs (requested via logprobs/top_logprobs in the
payload), each extracted term is annotated with <logprobs>:
  "logprob_mean": mean log-probability over the tokens of that term's `name`
  "logprob_min":  the least-confident token in that span
Lower (more negative) = less confident; 0 = certain. Aggregated by aligning the
response tokens back to each value span (see attach_logprobs).

The ACTION term additionally carries "top_logprobs": the model's top-N alternative
tokens at the first action token, each with its logprob. Because the action is a
CONTROLLED VOCABULARY (ALLOWED_ACTIONS), these alternatives are competing classes
(e.g. decreased vs increased), so the rank1−rank2 gap is a classification-confidence
margin. Object/process names are free text, so per-alternative distributions there are
not meaningful and are not stored (only logprob_mean/min are).

A per-response aggregate "_response_logprob" (mean logprob, perplexity, token count)
gives a coarse overall confidence comparable across models.

Raw per-token logprobs are NOT kept in kec.json (would bloat it). They are streamed to
a sidecar `kec_logprobs.jsonl` next to kec.json — one JSON line per KE:
  {"ke_id": "KE123", "content": "<raw model output>", "tokens": [{token, logprob, top_logprobs}, ...]}
so any aggregation can be recomputed later without re-running the models.

Persistence is incremental and crash-safe: the sidecar is appended per KE and kec.json
is checkpointed atomically every min(threads, 20) records, so a crash loses at most that
many KEs (the rest are resumable on the next run).

Ploomber task:
  upstream: ["parse_pydantic"]
  product:  {"json": "..."}   (copy to resources/kec.json for use by pydantic2solr)
  per-model config (models[model_key]): threads, max_tokens, logprobs, top_logprobs

Standalone:
  python tasks/extract_kec.py --json products/aop.json --output kec.json
  python tasks/extract_kec.py --json products/aop.json --output kec.json \\
      --base-url http://localhost:11434/v1 --model llama3.2
  python tasks/extract_kec.py --json products/aop.json --output kec.json \\
      --base-url https://api.openai.com/v1 --api-key sk-... --model gpt-4o-mini

Environment variables (alternative to CLI flags):
  LLM_BASE_URL   LLM_API_KEY   LLM_MODEL   LLM_TIMEOUT
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError

from tasks.kec_schema import ALLOWED_ACTIONS, AnnotatedTerm, KECTriple
from tasks.llm_client import ChatLLMClient, parse_json_block
from tasks.llm_logprobs import SpanAligner, response_logprob

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["parse_pydantic"]
product = None
enabled = None
models = None
model_key = None
ke_filter = None     # dict with keys: ke_ids, oecd_status, limit (all optional/None)
# Fail the task when too many KEs errored, so Ploomber does NOT mark it up-to-date and
# the next build retries them (kec.json is still written either way — the data is kept,
# only the task status changes). False/None = never fail (default); a float in (0,1] =
# fail when errored/total exceeds that fraction; an int >= 1 = fail at that many errors.
# A permanently-failing KE (e.g. "no_text") would wedge the pipeline forever under a
# strict setting, which is why the default is off and a fraction is the useful form.
fail_on_errors = None
# threads, flush_every and max_tokens are read from models[model_key] at runtime
# -

logger = logging.getLogger(__name__)


model_llm  = models[model_key]["model_llm"]
base_url   = models[model_key].get(
    "base_url", os.environ.get("AOP4ENM_LLM_BASE_URL", "http://localhost:8000/v1"))
api_key    = models[model_key].get("api_key","")
threads    = models[model_key].get("threads", 128)
# checkpoint cadence: write kec.json every N completed KEs (crash window = N KEs).
# default 1; raise it per-model if the rewrite cost hurts on large corpora.
flush_every  = models[model_key].get("flush_every", 1)
max_tokens   = models[model_key].get("max_tokens", 65536)
temperature  = models[model_key].get("temperature", 0.1)
request_logprobs = models[model_key].get("logprobs", True)
top_logprobs     = models[model_key].get("top_logprobs", 3)

print(base_url, model_llm)
# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------

# AnnotatedTerm, KECTriple and ALLOWED_ACTIONS come from tasks.kec_schema — one
# definition shared with extract_assays.py, so both extractors stay compatible with
# kec_compare and a fix to either benefits both (kec_schema.AnnotatedTerm tolerates a
# slot given as a bare string, which used to fail the whole KE here). The Field
# descriptions are documentation only: SYSTEM_PROMPT below is a literal and does not
# derive from the pydantic schema, so extraction is unaffected by the move.


class KECExtraction(BaseModel):
    """Extracted Key Event Components from description text."""
    triples: List[KECTriple] = Field(
        default_factory=list,
        description=(
            "Each entry is one coherent biological change: the specific object that changes, "
            "the process it participates in, and the action (direction/nature of change). "
            "Typically 1-5 triples per KE. Each triple must make biological sense as a unit."
        )
    )
    organ_terms: List[AnnotatedTerm] = Field(
        default_factory=list,
        description=(
            "Organs or tissues where the KE occurs. "
            "E.g. liver, lung, kidney, brain. Use UBERON or FMA IRIs."
        )
    )
    cell_terms: List[AnnotatedTerm] = Field(
        default_factory=list,
        description=(
            "Cell types involved. "
            "E.g. hepatocyte, macrophage, neuron. Use CL IRIs."
        )
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert in the AOP (Adverse Outcome Pathway) framework and biomedical ontologies.

Your task: Given an AOP-Wiki Key Event description, extract Key Event Components (KECs) as
coherent biological triples. Each triple describes ONE specific biological change:

  biological_object  — the entity that undergoes the change
                       (protein, receptor, organelle, cell, organ, DNA, lipid, etc.)
                       Preferred ontologies: PR, CHEBI, GO (cellular component), CL, UBERON, FMA

  biological_process — the process or pathway being altered
                       (apoptosis, oxidative phosphorylation, inflammation, etc.)
                       Preferred ontologies: GO (biological process), MeSH

  biological_action  — the direction or nature of the change.
                       MUST be one of these exact values (AOP-Wiki controlled vocabulary):
                         increased, decreased, occurrence, disrupted, abnormal,
                         arrested, delayed, premature, pathological,
                         functional change, morphological change
                       Set iri and source to null for actions (no ontology IRI needed).

Each slot may be null if not applicable to that change (e.g. a pure "activation" event
may have no distinct process). Extract 1-5 triples that together cover the KE.
Each triple must be semantically coherent — do NOT create all combinations.

ORGAN TERMS  — organs or tissues where the KE occurs (UBERON, FMA)
CELL TERMS   — specific cell types involved (CL)

ONTOLOGY IRI RULES (CRITICAL):
- Provide the FULL IRI (e.g. "http://purl.obolibrary.org/obo/GO_0006915" not just "GO:0006915")
- For GO:     http://purl.obolibrary.org/obo/GO_XXXXXXX
- For CL:     http://purl.obolibrary.org/obo/CL_XXXXXXX
- For CHEBI:  http://purl.obolibrary.org/obo/CHEBI_XXXXXXX
- For PR:     http://purl.obolibrary.org/obo/PR_XXXXXXX
- For UBERON: http://purl.obolibrary.org/obo/UBERON_XXXXXXX
- For FMA:    http://purl.org/sig/ont/fma/fmaNNNNNN
- NEVER INVENT IRIs. If unsure of the exact numeric ID, set iri to null.

Respond ONLY with valid JSON matching this schema — no markdown, no explanation:
{
  "triples": [
    {
      "biological_object":  {"name": "string", "iri": "string|null", "source": "string|null"},
      "biological_process": {"name": "string", "iri": "string|null", "source": "string|null"},
      "biological_action":  {"name": "string", "iri": "string|null", "source": "string|null"}
    }
  ],
  "organ_terms": [{"name": "string", "iri": "string|null", "source": "string|null"}],
  "cell_terms":  [{"name": "string", "iri": "string|null", "source": "string|null"}]
}
"""

# ---------------------------------------------------------------------------
# Logprob alignment (token-level → per-field aggregates)
# ---------------------------------------------------------------------------

def attach_logprobs(content: str, token_logprobs: list, result: dict) -> None:
    """
    Annotate each term in `result` (in-place) with per-field logprob aggregates,
    add a response-level aggregate, and stash the raw token array for the sidecar.

    Per-field: logprob_mean, logprob_min. Action terms also get top_logprobs
    (the classification margin against the controlled vocabulary). Token→span
    alignment is delegated to the shared SpanAligner (one left-to-right cursor
    across the whole response), so this stays in sync with extract_assays.py.
    """
    aligner = SpanAligner(token_logprobs)

    def _annotate(term, is_action=False):
        if not term or not term.get("name"):
            return
        agg = aligner.annotate(term["name"], categorical=is_action)
        if not agg:
            return
        if "logprob_mean" in agg:
            term["logprob_mean"] = agg["logprob_mean"]
            term["logprob_min"]  = agg["logprob_min"]
        if is_action and agg.get("top_logprobs"):
            term["top_logprobs"] = agg["top_logprobs"]

    for tr in result.get("triples", []):
        _annotate(tr.get("biological_object"))
        _annotate(tr.get("biological_process"))
        _annotate(tr.get("biological_action"), is_action=True)
    for term in result.get("organ_terms", []):
        _annotate(term)
    for term in result.get("cell_terms", []):
        _annotate(term)

    rl = response_logprob(token_logprobs)
    if rl:
        result["_response_logprob"] = rl
    # Stashed for the sidecar; popped out before kec.json is written.
    result["_logprob_tokens"]  = token_logprobs
    result["_logprob_content"] = content


# ---------------------------------------------------------------------------
# Response parser (plugs into the shared ChatLLMClient)
# ---------------------------------------------------------------------------
# The network/retry/reasoning-fallback loop now lives in tasks.llm_client
# .ChatLLMClient (shared with extract_assays.py); only the KEC-specific parsing
# stays here, supplied to ChatLLMClient.complete() as a `parser` callback so a
# JSON/validation failure still triggers a retry. Behaviour is unchanged from the
# old LLMClient.extract/_parse pair: parse JSON block → KECExtraction validation →
# controlled-vocabulary warning → per-field logprob attachment.

def _validate_kec(raw: dict) -> dict:
    """Validate the extraction, salvaging item-by-item if the object as a whole fails.

    Mirrors extract_assays._validate_assays. One malformed entry used to fail
    KECExtraction outright; the exception reaches ChatLLMClient.complete, which returns
    {"error": ...}, and EVERY triple the model found for that KE is lost. Better to keep
    the triples that are fine and drop only the bad ones. If every triple is bad the
    response really is broken — re-raise, so the retry/error path still applies."""
    try:
        return KECExtraction(**raw).model_dump()
    except ValidationError as e:
        obj_err = e
    if not isinstance(raw, dict):
        raise obj_err
    kept, dropped = dict(raw), []
    for field, model in (("triples", KECTriple),
                         ("organ_terms", AnnotatedTerm),
                         ("cell_terms", AnnotatedTerm)):
        items = raw.get(field)
        if not isinstance(items, list):
            continue
        good = []
        for i, item in enumerate(items):
            try:
                model.model_validate(item)
            except ValidationError as e:
                dropped.append((field, i, len(items), e))
            else:
                good.append(item)
        kept[field] = good
    if raw.get("triples") and not kept["triples"]:
        raise obj_err          # nothing worth keeping
    try:
        result = KECExtraction(**kept).model_dump()
    except ValidationError:
        raise obj_err          # the failure was not in the list items
    for field, i, n, e in dropped:
        logger.warning(f"dropped {field}[{i}] of {n} "
                       f"({len(kept[field])} kept): {str(e)[:300]}")
    return result


def parse_kec(content: str, token_logprobs: list | None,
              strip_think: bool = True) -> dict:
    """Validate the LLM JSON into a KECExtraction dict and attach logprob
    aggregates. Raises on parse failure, or if nothing validates
    (ChatLLMClient.complete retries); returns the annotated result dict on success."""
    raw = parse_json_block(content, strip_think=strip_think)
    result = _validate_kec(raw)
    # Warn on action values outside the controlled vocabulary
    for tr in result.get("triples", []):
        act = tr.get("biological_action")
        if act and act.get("name") and act["name"] not in ALLOWED_ACTIONS:
            logger.warning(f"Action outside vocabulary: {act['name']!r}")
    # Attach logprob aggregates + stash raw tokens for the sidecar
    if token_logprobs:
        attach_logprobs(content, token_logprobs, result)
    return result


def _get_default_client(model_override: str = None) -> ChatLLMClient:
    return ChatLLMClient(
        base_url=os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.environ.get("LLM_API_KEY", "ollama"),
        model=model_override or os.environ.get("LLM_MODEL", "llama3.2"),
        timeout=float(os.environ.get("LLM_TIMEOUT", "7200")),
        logger=logger,
    )


# ---------------------------------------------------------------------------
# HTML cleaning
# ---------------------------------------------------------------------------

def _clean_html(text: str) -> str:
    try:
        from tasks.utils import clean_html
        return clean_html(text)
    except ImportError:
        import re
        return re.sub(r"<[^>]+>", " ", text).replace("\xa0", " ").strip()


# ---------------------------------------------------------------------------
# Structured KEC extraction (from XML parsed data — no LLM needed)
# ---------------------------------------------------------------------------

def build_lookup(data) -> dict:
    """
    Build UUID → {name, iri, source} lookup dicts from parsed aopkb.Data.

    BiologicalTermType has: source_id (the IRI fragment or full IRI),
    source (ontology prefix e.g. "GO", "CHEBI", "PR"), name.

    We normalise source_id to a full IRI where we can.
    """
    def _normalise_iri(source: str, source_id: str) -> Optional[str]:
        """Convert e.g. GO:0006915 → full IRI, or return as-is if already full."""
        if not source_id or source_id in ("N/A", ""):
            return None
        if source_id.startswith("http"):
            return source_id
        # Try to build standard OBO Foundry IRI
        prefixes = {
            "GO":     "http://purl.obolibrary.org/obo/GO_",
            "CL":     "http://purl.obolibrary.org/obo/CL_",
            "CHEBI":  "http://purl.obolibrary.org/obo/CHEBI_",
            "UBERON": "http://purl.obolibrary.org/obo/UBERON_",
            "PR":     "http://purl.obolibrary.org/obo/PR_",
            "FMA":    "http://purl.org/sig/ont/fma/fma",
            "MESH":   "http://id.nlm.nih.gov/mesh/",
            "MP":     "http://purl.obolibrary.org/obo/MP_",
            "PCO":    "http://purl.obolibrary.org/obo/PCO_",
            "TAIR":   None,  # not a standard OBO prefix
        }
        base = prefixes.get(source.upper())
        if base is None:
            return source_id   # return as-is (may be a local ID)
        # Strip prefix from source_id if present
        clean = source_id
        for p in [f"{source}:", f"{source.upper()}:", f"{source.lower()}:"]:
            if clean.startswith(p):
                clean = clean[len(p):]
                break
        return f"{base}{clean}"

    def _term(bio_term) -> dict:
        src = getattr(bio_term, "source", None) or ""
        sid = getattr(bio_term, "source_id", None) or ""
        return {
            "name":   bio_term.name,
            "iri":    _normalise_iri(src, sid),
            "source": src or None,
        }

    obj_lookup  = {bo.id: _term(bo) for bo in data.biological_object}
    proc_lookup = {bp.id: _term(bp) for bp in data.biological_process}
    act_lookup  = {ba.id: _term(ba) for ba in data.biological_action}
    return {"object": obj_lookup, "process": proc_lookup, "action": act_lookup}


def extract_structured(ke, lookup: dict) -> list[dict]:
    """
    Extract already-curated KECs from the KE's biological_events field.
    Each BiologicalEvent has optional object_id, process_id, and required action_id.
    """
    if not ke.biological_events:
        return []
    results = []
    for evt in ke.biological_events.biological_event:
        triple = {}
        if evt.object_id:
            triple["object"] = lookup["object"].get(evt.object_id, {"name": evt.object_id, "iri": None, "source": None})
        if evt.process_id:
            triple["process"] = lookup["process"].get(evt.process_id, {"name": evt.process_id, "iri": None, "source": None})
        if evt.action_id:
            triple["action"] = lookup["action"].get(evt.action_id, {"name": evt.action_id, "iri": None, "source": None})
        results.append(triple)
    return results


def extract_organ_cell_structured(ke) -> dict:
    """Extract organ_term and cell_term from the KE if present (already structured)."""
    result = {}
    if ke.organ_term:
        src = getattr(ke.organ_term, "source", None) or ""
        sid = getattr(ke.organ_term, "source_id", None) or ""
        result["organ_term"] = {
            "name": ke.organ_term.name,
            "iri": sid if sid.startswith("http") else None,
            "source": src or None,
        }
    if ke.cell_term:
        src = getattr(ke.cell_term, "source", None) or ""
        sid = getattr(ke.cell_term, "source_id", None) or ""
        result["cell_term"] = {
            "name": ke.cell_term.name,
            "iri": sid if sid.startswith("http") else None,
            "source": src or None,
        }
    return result


# ---------------------------------------------------------------------------
# Text preparation for LLM
# ---------------------------------------------------------------------------

def build_ke_text(ke) -> str:
    """
    Compose input text for the LLM from KE fields.
    Uses title + short_name + description (primary source for KECs).
    Biological organisation level adds important context.
    """
    parts = []
    title = getattr(ke, "title", None) or ""
    short = getattr(ke, "short_name", None) or ""
    level = getattr(ke, "biological_organization_level", None)

    if title:
        parts.append(f"Title: {title}")
    if short and short != title:
        parts.append(f"Short name: {short}")
    if level:
        lv = level.value if hasattr(level, "value") else str(level)
        parts.append(f"Biological organisation level: {lv}")

    desc = getattr(ke, "description", None) or ""
    if desc.strip():
        parts.append(f"Description:\n{_clean_html(desc)[:3000]}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Wiki-ID helper (UUID → KE123 format)
# ---------------------------------------------------------------------------

def build_wiki_id_map(data) -> dict:
    """
    Build UUID → KEnnn map from vendor_specific key-event-reference entries,
    which carry the stable aop-wiki-id attribute.
    """
    mapping = {}
    for entry in data.vendor_specific.any_element:
        if "key-event-reference" not in entry.get("qname", ""):
            continue
        attrs = entry.get("attributes", {})
        uuid = attrs.get("id")
        wiki_num = attrs.get("aop-wiki-id")
        if uuid and wiki_num:
            mapping[uuid] = f"KE{wiki_num}"
    return mapping


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def _process_ke(ke, wiki_id: str, lookup: dict, client: ChatLLMClient,
                enabled: bool, dry_run: bool) -> tuple[str, dict, Optional[str]]:
    """Process a single KE; returns (wiki_id, record, err). Thread-safe.

    `err` is None on success, else the failure message — the caller routes it to
    kec_errors.json rather than storing it on the record, so an errored KE keeps its
    curated `structured` payload while `llm_extracted` stays None (which is what marks
    it for retry on the next run).
    """
    uuid = ke.id
    structured   = extract_structured(ke, lookup)
    organ_cell   = extract_organ_cell_structured(ke)

    record = {
        "ke_id":            wiki_id,
        "uuid":             uuid,
        "title":            ke.title,
        "biological_level": (ke.biological_organization_level.value
                             if ke.biological_organization_level else None),
        "structured":       structured,
        "n_structured":     len(structured),
        "llm_extracted":    None,
    }
    if organ_cell.get("organ_term"):
        record["organ_term_structured"] = organ_cell["organ_term"]
    if organ_cell.get("cell_term"):
        record["cell_term_structured"] = organ_cell["cell_term"]

    err = None
    if enabled:
        ke_text = build_ke_text(ke)
        if not ke_text.strip():
            err = "no_text"
        elif dry_run:
            logger.info(f"[DRY RUN] {wiki_id}: {ke.title[:60]}")
            record["llm_extracted"] = {"triples": [], "organ_terms": [], "cell_terms": []}
        else:
            logger.info(f"Processing {wiki_id} ({len(structured)} structured KECs)")
            extraction = client.complete(SYSTEM_PROMPT, f"Key Event:\n\n{ke_text}",
                                         parse_kec)
            if "error" in extraction:
                err = extraction["error"]
            else:
                record["llm_extracted"] = extraction
                logger.info(f"  {wiki_id} → triples={len(extraction.get('triples', []))}")

    return wiki_id, record, err


def process_all(data, client: ChatLLMClient, enabled: bool,
                ke_id_filter: Optional[set] = None,
                limit: Optional[int] = None,
                resume_ids: Optional[set] = None,
                dry_run: bool = False,
                threads: int = 1,
                on_result=None) -> dict:
    """
    Process all KEs. Returns results_dict keyed by wiki-id.
    Use threads > 1 for parallel LLM calls (I/O-bound, safe with ThreadPoolExecutor).

    on_result(wiki_id, record, err): optional callback invoked from the main thread as
    each KE completes — use it to persist incrementally (crash-safety). `err` is None
    on success, else the failure message. The as_completed loop is single-threaded, so
    the callback needs no locking.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    lookup   = build_lookup(data)
    wiki_ids = build_wiki_id_map(data)

    # Build the work list first (respects filter/limit/resume)
    work = []
    n_skip = 0
    for ke in data.key_event:
        uuid    = ke.id
        wiki_id = wiki_ids.get(uuid, uuid)
        if ke_id_filter and wiki_id not in ke_id_filter and uuid not in ke_id_filter:
            continue
        if resume_ids and wiki_id in resume_ids:
            n_skip += 1
            continue
        if limit and len(work) >= limit:
            break
        work.append((ke, wiki_id))

    logger.info(f"Queued {len(work)} KEs (skipped {n_skip} already done), threads={threads}")

    results = {}
    n_err   = 0

    def _submit(ke, wiki_id):
        return _process_ke(ke, wiki_id, lookup, client, enabled, dry_run)

    n_total = len(work)
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = {pool.submit(_submit, ke, wiki_id): wiki_id for ke, wiki_id in work}
        for i, future in enumerate(as_completed(futures), 1):
            wiki_id, record, err = future.result()
            if err:
                n_err += 1
            results[wiki_id] = record
            if on_result is not None:
                on_result(wiki_id, record, err)
            status = f"error ({err})" if err else f"ok ({len((record.get('llm_extracted') or {}).get('triples') or [])} triples)"
            logger.info(f"[{i}/{n_total}] {wiki_id} → {status}")

    logger.info(f"Completed: ok={len(results)}, skipped={n_skip}, errors={n_err}")
    return results


# ---------------------------------------------------------------------------
# OECD filter helper
# ---------------------------------------------------------------------------

def collect_ke_uuids_for_oecd(data, status_strings) -> set:
    """
    Return the set of KE UUIDs that belong to at least one AOP whose
    oecd_status matches any of the given status strings (case-insensitive
    substring match). Accepts a single string or a list.
    E.g. "endorsed" matches "WPHA/WNT Endorsed";
         ["WPHA/WNT Endorsed", "ESCA Approved"] matches those exactly.
    """
    if isinstance(status_strings, str):
        status_strings = [status_strings]
    wanted = [s.strip().lower() for s in status_strings]
    uuids = set()
    for aop in data.aop:
        aop_status = aop.status.oecd_status if aop.status else None
        if aop_status is None:
            continue
        val = (aop_status.value if hasattr(aop_status, "value") else str(aop_status)).lower()
        if any(w in val for w in wanted):
            if aop.key_events:
                for ke_ref in aop.key_events.key_event:
                    uuids.add(ke_ref.key_event_id)
    return uuids


# ---------------------------------------------------------------------------
# Ploomber entry point
# ---------------------------------------------------------------------------

def _run_ploomber(client: ChatLLMClient):
    from aopkb import Data
    from tasks.utils import preprocess_timestamps

    with open(upstream["parse_pydantic"]["json"], "r", encoding="utf-8") as f:
        json_data = json.load(f)
    preprocess_timestamps(json_data)
    data = Data.model_validate(json_data)

    # Unpack ke_filter dict from params
    _f = ke_filter or {}
    _ke_ids     = _f.get("ke_ids")
    _oecd       = _f.get("oecd_status")
    _limit      = _f.get("limit")

    # Build KE id filter
    filter_ids: Optional[set] = None
    if _ke_ids:
        filter_ids = set(_ke_ids)
    if _oecd:
        oecd_uuids = collect_ke_uuids_for_oecd(data, _oecd)
        logger.info(f"OECD filter '{_oecd}' → {len(oecd_uuids)} KE UUIDs")
        filter_ids = (filter_ids & oecd_uuids) if filter_ids else oecd_uuids

    kec_path    = product["json"]
    sidecar     = Path(kec_path).with_name("kec_logprobs.jsonl")
    errors_path = Path(kec_path).with_name("kec_errors.json")

    # Load existing results and skip already-processed KEs
    all_results = {}
    try:
        with open(kec_path, encoding="utf-8") as f:
            all_results = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # "done" = the LLM actually produced a result. A KE present but with
    # llm_extracted null (errored this run, or an older run that stored the message
    # inline as llm_error) is NOT done and gets retried.
    done_ids = {k for k, r in all_results.items()
                if (r or {}).get("llm_extracted") is not None}
    if done_ids:
        logger.info(f"Resuming: {len(done_ids)} KEs already extracted, skipping them")
    _n_retry = len(all_results) - len(done_ids)
    if _n_retry:
        logger.info(f"{_n_retry} KEs present without an LLM result — will retry")

    # durable per-KE error record; pruned as KEs succeed, so it lists what is
    # currently failing rather than a growing history
    errors = {}
    try:
        with open(errors_path, encoding="utf-8") as f:
            errors = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if errors:
        logger.info(f"{len(errors)} KEs errored previously — will retry")

    # Incremental, crash-safe persistence: append each completed KE's raw token
    # logprobs to the sidecar immediately, and checkpoint kec.json atomically
    # every FLUSH_EVERY records so a crash mid-run loses at most that many.
    FLUSH_EVERY = max(1, int(flush_every or 20))
    logger.info(f"checkpoint cadence: flush kec.json every {FLUSH_EVERY} KEs")
    state = {"since_flush": 0}
    sidecar_f = open(sidecar, "a", encoding="utf-8")

    def on_result(wiki_id, record, err):
        if err:
            errors[wiki_id] = {"error": str(err),
                               "title": record.get("title"),
                               "ts": datetime.now().isoformat(timespec="seconds")}
        else:
            _pop_tokens_to_sidecar(sidecar_f, wiki_id, record)
            record.pop("llm_error", None)   # clear any stale value from an older run
            errors.pop(wiki_id, None)       # succeeded now
        all_results[wiki_id] = record
        state["since_flush"] += 1
        if state["since_flush"] >= FLUSH_EVERY:
            _atomic_write_json(kec_path, all_results)
            _atomic_write_json(errors_path, errors)
            state["since_flush"] = 0

    try:
        process_all(data, client, enabled=bool(enabled),
                    ke_id_filter=filter_ids, limit=_limit,
                    resume_ids=done_ids, threads=int(threads or 1),
                    on_result=on_result)
    finally:
        sidecar_f.close()
        _atomic_write_json(kec_path, all_results)   # final flush, even on error
        _atomic_write_json(errors_path, errors)

    logger.info(f"Written {len(all_results)} records → {kec_path} "
                f"(errors={len(errors)}, see {errors_path.name})")

    # The caller applies the error budget *after* the stats CSV is written, so a
    # failing run still produces every declared product.
    return len(errors), len(all_results), errors_path


def _check_error_budget(n_err: int, n_total: int, errors_path) -> None:
    """Raise when the error count exceeds `fail_on_errors`, so Ploomber records the
    task as failed and retries the errored KEs on the next build.

    The products are already written by the caller's finally block, so failing here
    costs no data — it only withholds the up-to-date stamp (and blocks downstream
    tasks, which is the point: a model that failed on half the corpus should not
    silently produce a model_summary).
    """
    if not fail_on_errors or not n_err:
        return
    thr = float(fail_on_errors)
    if thr <= 0:
        return
    if thr < 1:                                  # fraction of processed KEs
        frac = n_err / n_total if n_total else 1.0
        over, limit_s, actual_s = frac > thr, f"{thr:.1%}", f"{frac:.1%}"
    else:                                        # absolute count
        over, limit_s, actual_s = n_err > thr, f"{int(thr)}", f"{n_err}"
    if over:
        raise RuntimeError(
            f"extract_kec: {n_err}/{n_total} KEs errored ({actual_s}), over the "
            f"fail_on_errors limit of {limit_s}. kec.json and "
            f"{Path(errors_path).name} were written; the task is marked failed so the "
            f"next build retries those KEs. Inspect {Path(errors_path).name} — if the "
            f"failures are structural (e.g. context length), fix the model config "
            f"first, since a retry alone will reproduce them."
        )
    logger.info(f"error budget OK: {n_err}/{n_total} ({actual_s}) within {limit_s}")


def _pop_tokens_to_sidecar(sidecar_f, wiki_id: str, record: dict) -> None:
    """Move raw token logprobs out of the record into the (open) sidecar file,
    so kec.json keeps only aggregates. Flushed immediately for crash-safety.
    Also records prompt length / token usage (prompt_chars always; prompt_tokens etc.
    when the backend returns a `usage` object) — a line is written even with logprobs
    off, so usage stats cover every model."""
    llm = record.get("llm_extracted") or {}
    tokens  = llm.pop("_logprob_tokens", None)
    content = llm.pop("_logprob_content", None)
    usage   = llm.pop("_usage", None)
    if tokens is None and usage is None:
        return
    line = {"ke_id": wiki_id, "content": content, "tokens": tokens}
    if usage:
        line.update(usage)        # prompt_chars, prompt_tokens, completion_tokens, total
    sidecar_f.write(json.dumps(line, ensure_ascii=False) + "\n")
    sidecar_f.flush()


def _atomic_write_json(path, obj) -> None:
    """Write JSON to a temp file then os.replace — never leaves a half-written
    kec.json if the process dies mid-write."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=4)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _parse_args():
    p = argparse.ArgumentParser(
        description="Extract Key Event Components (KECs) from AOP-Wiki KE text",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--json",  help="Path to pre-parsed aop.json (parse_pydantic product)")
    p.add_argument("--xml",   help="Path to AOP-Wiki XML (fallback if --json not given)")
    p.add_argument("--output", default="kec_extracted.json")
    p.add_argument("--ke-ids", nargs="*",
                   help="Only process these KE IDs (e.g. KE1392 KE881)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--base-url",
                   default=os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1"))
    p.add_argument("--api-key",
                   default=os.environ.get("LLM_API_KEY", "ollama"))
    p.add_argument("--model",
                   default=os.environ.get("LLM_MODEL", "llama3.2"))
    p.add_argument("--timeout", type=float, default=90.0)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--threads", type=int, default=1)
    p.add_argument("--max-tokens", type=int, default=65536)
    p.add_argument("--structured-only", action="store_true",
                   help="Only extract structured KECs (no LLM calls)")
    p.add_argument("--flush-every", type=int, default=1,
                   help="Checkpoint the output JSON every N KEs (default: every KE)")
    p.add_argument("--resume", action="store_true",
                   help="Skip KE IDs already extracted in the output file; KEs that "
                        "errored (llm_extracted null) are retried")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


def _load_data(args):
    from aopkb import Data
    from tasks.utils import preprocess_timestamps

    if args.json:
        with open(args.json, "r", encoding="utf-8") as f:
            raw = json.load(f)
        preprocess_timestamps(raw)
        return Data.model_validate(raw)

    if args.xml:
        try:
            from aopkb import parse_xml
            return parse_xml(args.xml)
        except (ImportError, AttributeError):
            from xsdata_pydantic.bindings import XmlParser
            parser = XmlParser()
            with open(args.xml, "rb") as f:
                return parser.parse(f, Data)

    raise ValueError("Provide --json or --xml")


def _load_done_ids(path: str) -> set:
    """KEs that already have an LLM result. Records with llm_extracted null (errored,
    or a legacy record carrying the old inline llm_error) are not done — they retry."""
    done = set()
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
        done = {k for k, r in existing.items()
                if (r or {}).get("llm_extracted") is not None}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return done


def _run_standalone(args):
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    data      = _load_data(args)
    client    = _get_default_client(model_override=args.model)
    client.max_tokens = args.max_tokens
    done_ids  = _load_done_ids(args.output) if args.resume else set()
    filter_ids = set(args.ke_ids) if args.ke_ids else None

    sidecar     = Path(args.output).with_name("kec_logprobs.jsonl")
    errors_path = Path(args.output).with_name("kec_errors.json")

    # Preserve existing entries on resume — including errored ones (which are absent
    # from done_ids but must not be dropped from the output).
    all_results = {}
    errors = {}
    if args.resume:
        try:
            with open(args.output, encoding="utf-8") as f:
                all_results = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        try:
            with open(errors_path, encoding="utf-8") as f:
                errors = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    llm_enabled = not args.structured_only

    # Incremental, crash-safe persistence (same as the Ploomber path)
    FLUSH_EVERY = max(1, int(args.flush_every or 20))
    state = {"since_flush": 0}
    sidecar_f = open(sidecar, "a", encoding="utf-8")

    def on_result(wiki_id, record, err):
        if err:
            errors[wiki_id] = {"error": str(err),
                               "title": record.get("title"),
                               "ts": datetime.now().isoformat(timespec="seconds")}
        else:
            _pop_tokens_to_sidecar(sidecar_f, wiki_id, record)
            record.pop("llm_error", None)   # clear any stale value from an older run
            errors.pop(wiki_id, None)       # succeeded now
        all_results[wiki_id] = record
        state["since_flush"] += 1
        if state["since_flush"] >= FLUSH_EVERY:
            _atomic_write_json(args.output, all_results)
            _atomic_write_json(errors_path, errors)
            state["since_flush"] = 0

    try:
        process_all(
            data, client,
            enabled=llm_enabled,
            ke_id_filter=filter_ids,
            limit=args.limit,
            resume_ids=done_ids,
            dry_run=args.dry_run,
            threads=args.threads,
            on_result=on_result,
        )
    finally:
        sidecar_f.close()
        _atomic_write_json(args.output, all_results)
        _atomic_write_json(errors_path, errors)

    logger.info(f"Written {len(all_results)} records → {args.output} "
                f"(errors={len(errors)}, see {errors_path.name})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if enabled:
    from pathlib import Path
    _out_dir = Path(product["json"]).parent
    _out_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(_out_dir / "extract_kec.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )

    # from_config picks up base_url / api_key / extra_body / referer / title (incl.
    # OpenRouter). max_tokens (65536) and top_logprobs (3) now match from_config's own
    # defaults, so passing them is redundant — kept explicit to pin this task's values.
    with ChatLLMClient.from_config(
        models[model_key],
        logger=logger,
        max_tokens=int(max_tokens or 65536),
        top_logprobs=int(top_logprobs or 3)
    ) as _client:
        print(_client.endpoint, _client.model)
        start = time.perf_counter()
        _n_err, _n_recs, _errors_path = _run_ploomber(_client)
        elapsed = time.perf_counter() - start
        print(f"\t{_client.endpoint}\t{_client.model}\tElapsed time: {elapsed:.3f} seconds")

    # ── per-task stats CSV ───────────────────────────────────────────────────────
    # ALL columns are CUMULATIVE over the whole corpus, not per-run: n_ke /
    # n_llm_triples are re-derived from kec.json (which grows across resumes),
    # n_llm_errors is the current kec_errors.json (pruned as KEs recover), and
    # elapsed_s carries the previous total forward. `elapsed_this_run_s` is the only
    # single-run figure. The file is overwritten each run — it is a snapshot of the
    # cumulative state, not a run log.
    try:
        import pandas as pd
        with open(product["json"], encoding="utf-8") as _f:
            _res = json.load(_f)
        _n_ke      = len(_res)
        _n_triples = sum(len((r.get("llm_extracted") or {}).get("triples", [])) for r in _res.values())
        # errors now live in the sidecar, not inline on the record
        try:
            with open(Path(product["json"]).with_name("kec_errors.json"),
                      encoding="utf-8") as _ef:
                _n_errors = len(json.load(_ef))
        except (FileNotFoundError, json.JSONDecodeError):
            _n_errors = 0
        # elapsed_s accumulates across resumed runs, like every other column here
        # (which are re-derived from the cumulative kec.json): carry the previous
        # total forward and add this run's time, so the figure covers the whole
        # corpus however many resumes it took.
        _prev_elapsed = 0.0
        try:
            _prev = pd.read_csv(product["csv"])
            if len(_prev) and "elapsed_s" in _prev.columns:
                _v = _prev["elapsed_s"].iloc[0]
                _prev_elapsed = float(_v) if pd.notna(_v) else 0.0
        except (FileNotFoundError, ValueError, KeyError):
            pass
        pd.DataFrame([{
            "model_key":            model_key,
            # kept as `elapsed_s` for model_summary.py:61, now cumulative like the
            # other columns; the aliases spell out which is which for readers.
            "elapsed_s":            round(_prev_elapsed + elapsed, 1),
            "elapsed_cumulative_s": round(_prev_elapsed + elapsed, 1),
            "elapsed_this_run_s":   round(elapsed, 1),
            "n_ke":                 _n_ke,
            "n_llm_triples":        _n_triples,
            "n_llm_errors":         _n_errors,
        }]).to_csv(product["csv"], index=False)
    except Exception as _e:
        logger.warning(f"stats CSV write failed: {_e}")

    # Applied last: every product above is on disk, so failing here loses nothing and
    # only withholds the up-to-date stamp (see _check_error_budget).
    _check_error_budget(_n_err, _n_recs, _errors_path)