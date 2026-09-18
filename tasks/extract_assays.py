"""
extract_assays.py — Per-assay Methods2AOP metadata extraction from AOP-Wiki KE text.

Ploomber task: reads upstream["parse_pydantic"]["json"] (aopkb.Data),
writes product["json"]      (per-KE dump: wiki-id → list of per-assay records)
       + sidecar assays_logprobs.jsonl (raw token logprobs, one line per KE)
       + sidecar assays_errors.json    (wiki-id → {error, source_field, ts}; errored
         KEs stay absent from assays.json so they retry, and are pruned here on success)

The KE measurement_methodology field describes MULTIPLE assays in one block;
Methods2AOP requires SEPARATE structured metadata per assay.

Trust signals (mirrors extract_kec.py — there is NO ground truth for assays):
  * per-field token log-probabilities (logprob_mean / logprob_min);
  * for the controlled-vocabulary fields method_type / endpoint_direction / throughput,
    the top-logprobs **margin** (rank1 − rank2) — a classification-confidence signal,
    the direct analogue of the KEC action margin;
  * an `evidence` list per assay — verbatim span(s) from the source text the extraction
    is based on, enabling a judge-free faithfulness check (assay_evidence_check.py) and
    curator verifiability;
  * a `kec_triple` per assay — the biological change the assay measures, in the same shape
    extract_kec.py produces from the KE *description*. The two are independent reads of
    different KE fields, so agreement between them is a cross-check; tasks/kec_compare.py
    consumes this shape as-is (comparison itself is a separate task).

Output structure (assays.json):
  {
    "<ke-uuid>": [
      {
        "assay_name": "DAPI", "description": "...", "method_type": "in_vitro",
        "cell_types": [...], "organ_tissue": [...], "species": [...],
        "measured_endpoint": "cell count", "endpoint_direction": "decrease",
        "ontology_terms": [{"name","iri","source"}], "guideline": null,
        "throughput": "high",
        "evidence": ["... verbatim source phrase ..."],
        "kec_triple": {
          "biological_object":  {"name": "hepatocyte", "iri": "...", "source": "CL"},
          "biological_process": {"name": "apoptosis",  "iri": "...", "source": "GO"},
          "biological_action":  {"name": "increased",  "iri": null,  "source": null}
        },
        "logprobs": {
          "assay_name": {"logprob_mean": -0.1, "logprob_min": -0.3},
          "endpoint_direction": {"logprob_mean": -0.2, "top_logprobs": [...], "margin": 1.8},
          "kec_triple.biological_action": {"logprob_mean": -0.2, "margin": 2.1},
          ...
        }
      }, ...
    ], ...
  }

Parameters (injected by Ploomber): see header below.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from tasks.aop_select import build_wiki_id_map, ke_filter_uuids
from tasks.assay_models import AssayList, AssayRecord
from tasks.kec_schema import ALLOWED_ACTIONS
from tasks.llm_client import ChatLLMClient, parse_json_block
from tasks.llm_logprobs import SpanAligner, response_logprob, margin
from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["parse_pydantic"]
product = None
enabled = False
models = None
model_key = None
ke_filter = None     # {ke_ids, oecd_status, limit} — same filter as extract_kec
# -

logger = setup_logging(Path(product["json"]).parent / "extract_assays.log")

model_llm = models[model_key]["model_llm"]
base_url = models[model_key].get(
    "base_url", os.environ.get("AOP4ENM_LLM_BASE_URL", "http://localhost:8000/v1"))
api_key = models[model_key].get("api_key", "")
threads = models[model_key].get("threads", 128)
# checkpoint cadence: write assays.json every N completed KEs (crash window = N KEs).
# default 20; set per-model lower to flush more often (e.g. slow remote runs).
flush_every = models[model_key].get("flush_every", 1)
max_tokens = models[model_key].get("max_tokens", 65536)
temperature = models[model_key].get("temperature", 0.1)
timeout_s = models[model_key].get("timeout", 7200.0)
request_logprobs = models[model_key].get("logprobs", True)
top_logprobs = models[model_key].get("top_logprobs", 3)

# Controlled-vocabulary fields → carry a top-logprobs classification margin
CATEGORICAL_FIELDS = ("method_type", "endpoint_direction", "throughput")
# Scalar fields aligned to token spans (document order keeps the cursor monotonic)
LOGPROB_FIELDS = ("assay_name", "description", "method_type",
                  "measured_endpoint", "endpoint_direction", "throughput")

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_ONTOLOGY_HINT = """
Preferred ontologies (use full IRIs only when confident — NEVER invent):
  BAO  (BioAssay Ontology):         http://www.bioassayontology.org/bao#BAO_XXXXXXX
  CHMO (Chemical Methods Ontology): http://purl.obolibrary.org/obo/CHMO_XXXXXXX
  OBI  (Biomed. Investigations):    http://purl.obolibrary.org/obo/OBI_XXXXXXX
  GO   (Gene Ontology):             http://purl.obolibrary.org/obo/GO_XXXXXXX
  EFO  (Experimental Factor Onto.): http://www.ebi.ac.uk/efo/EFO_XXXXXXX
  FIX  (Physico-chem. methods):     http://purl.obolibrary.org/obo/FIX_XXXXXXX
  CL   (Cell Ontology):             http://purl.obolibrary.org/obo/CL_XXXXXXX
  UBERON (Anatomy):                 http://purl.obolibrary.org/obo/UBERON_XXXXXXX
  NCBITaxon (Species):              http://purl.obolibrary.org/obo/NCBITaxon_XXXXXXX
"""

SYSTEM_PROMPT = f"""You are an expert in biomedical assay annotation and the AOP (Adverse Outcome Pathway) framework.

You will receive the measurement methodology description of an AOP-Wiki Key Event.
This text may describe MULTIPLE distinct laboratory assays.

Your task: extract SEPARATE structured metadata for EACH distinct assay mentioned.
Do NOT merge multiple assays into one record. Do NOT invent assays not in the text.

For EACH assay produce ONE JSON object with these fields:

assay_name        — short normalised lab name (e.g. "DAPI", "MTT", "western blot").
description       — 1-2 sentences: what this specific assay measures and how.
method_type       — one of: in_vitro | in_vivo | in_silico | ex_vivo | not_specified
cell_types        — cell lines / primary cells used WITH THIS assay. Empty if not stated.
organ_tissue      — organs or tissues relevant to this assay. Empty if not mentioned.
species           — species applicability for this assay. Empty if not stated.
measured_endpoint — what this assay quantifies (e.g. "cell count", "DNA strand breaks").
endpoint_direction — one of: increase | decrease | change | not_specified
ontology_terms    — max 5 controlled terms for WHAT IS MEASURED. name + IRI (iri=null if unsure).
guideline         — formal guideline for this assay (e.g. "OECD TG 487"); null if none.
throughput        — one of: high | medium | low | not_specified
evidence          — 1-3 VERBATIM spans copied EXACTLY from the input text that justify this
                    assay and its fields. Quote word-for-word; do NOT paraphrase or summarise.
kec_triple        — the ONE coherent biological change THIS assay measures, as a triple:
                      biological_object  — the entity that undergoes the change
                                           (protein, receptor, organelle, cell, organ, DNA, ...)
                                           Preferred: PR, CHEBI, GO (cellular component), CL, UBERON
                      biological_process — the process or pathway being altered
                                           (apoptosis, inflammation, ...). Preferred: GO, MeSH
                      biological_action  — direction/nature of the change. MUST be one of:
                                           {", ".join(sorted(ALLOWED_ACTIONS))}
                                           Set iri and source to null for actions.
                    Do NOT merely restate measured_endpoint/endpoint_direction: name the
                    underlying biological change the measurement is evidence of. Any slot may
                    be null if not applicable; set kec_triple itself to null if the text
                    supports no coherent biological change.

{_ONTOLOGY_HINT}

CRITICAL RULES:
- One JSON object per assay. If 4 assays are mentioned → 4 objects.
- NEVER invent IRIs. Prefer iri=null over a guessed IRI.
- `evidence` must be an exact substring of the input — copy, never rephrase.
- cell_types, organ_tissue, species should reflect what is stated for THAT assay.
- Respond ONLY with valid JSON. No markdown, no explanation.

JSON schema (array of assay objects):
{{
  "assays": [
    {{
      "assay_name": "string", "description": "string", "method_type": "string",
      "cell_types": ["string"], "organ_tissue": ["string"], "species": ["string"],
      "measured_endpoint": "string", "endpoint_direction": "string",
      "ontology_terms": [{{"name": "string", "iri": "string|null", "source": "string|null"}}],
      "guideline": "string|null", "throughput": "string",
      "evidence": ["string"],
      "kec_triple": {{
        "biological_object":  {{"name": "string", "iri": "string|null", "source": "string|null"}},
        "biological_process": {{"name": "string", "iri": "string|null", "source": "string|null"}},
        "biological_action":  {{"name": "string", "iri": null, "source": null}}
      }}
    }}
  ]
}}
"""


# ---------------------------------------------------------------------------
# Per-assay logprob annotation
# ---------------------------------------------------------------------------

def attach_assay_logprobs(tokens: list, assays: list) -> None:
    """Annotate each assay's scalar fields (in document order) with logprob
    aggregates; controlled-vocab fields also get top_logprobs + margin."""
    aligner = SpanAligner(tokens)
    for rec in assays:
        lp: dict = {}
        for field in LOGPROB_FIELDS:
            val = rec.get(field)
            if not isinstance(val, str) or not val:
                continue
            agg = aligner.annotate(val, categorical=(field in CATEGORICAL_FIELDS))
            if not agg:
                continue
            if field in CATEGORICAL_FIELDS and agg.get("top_logprobs"):
                m = margin(agg["top_logprobs"])
                if m is not None:
                    agg["margin"] = m
            lp[field] = agg
        for slot, term in (rec.get("kec_triple") or {}).items():
            name = (term or {}).get("name")
            if not name:
                continue
            is_action = slot == "biological_action"
            agg = aligner.annotate(name, categorical=is_action)
            if not agg:
                continue
            if is_action and agg.get("top_logprobs"):
                m = margin(agg["top_logprobs"])
                if m is not None:
                    agg["margin"] = m
            lp[f"kec_triple.{slot}"] = agg
        if lp:
            rec["logprobs"] = lp


# ---------------------------------------------------------------------------
# Response parser (plugs into the shared ChatLLMClient)
# ---------------------------------------------------------------------------

def _validate_assays(raw: dict) -> list:
    """Validate the assay list, salvaging record-by-record if the list as a whole fails.

    Validating only the whole AssayList made ONE malformed field cost the entire KE:
    the exception propagates to ChatLLMClient.complete, which returns {"error": ...},
    and the caller then drops every assay the model found for that KE. So on failure,
    re-validate each record on its own and keep the ones that pass. If none does, the
    response really is broken — re-raise, so the retry/error path still applies."""
    try:
        return [rec.model_dump() for rec in AssayList(**raw).assays]
    except ValidationError as e:
        list_err = e
    items = raw.get("assays") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise list_err
    kept, dropped = [], []
    for i, item in enumerate(items):
        try:
            kept.append(AssayRecord(**item).model_dump())
        except (ValidationError, TypeError) as e:
            dropped.append((i, e))
    if not kept:
        raise list_err
    for i, e in dropped:
        logger.warning(f"dropped assay {i} of {len(items)} "
                       f"({len(kept)} kept): {str(e)[:300]}")
    return kept


def parse_assays(content: str, token_logprobs: list | None,
                 strip_think: bool = True) -> dict:
    """Validate the LLM JSON into per-assay records and attach logprob aggregates.
    Returns {"assays": [...], "_logprob_tokens"/"_logprob_content"/"_response_logprob"}.
    Raises on parse failure, or if no assay validates (ChatLLMClient.complete retries)."""
    raw = parse_json_block(content, strip_think=strip_think)
    if isinstance(raw, list):          # accept a bare [...] as the assays list
        raw = {"assays": raw}
    assays = _validate_assays(raw)
    # The action slot is a controlled vocabulary, not a free ontology term: it is stored
    # as a term dict only so it lines up with the AOP-Wiki reference actions kec_compare
    # matches against (those do carry source/iri) — its own iri/source stay null. Warn
    # on values outside the vocabulary, as parse_kec does.
    for rec in assays:
        act = (rec.get("kec_triple") or {}).get("biological_action")
        if act and act.get("name") and act["name"] not in ALLOWED_ACTIONS:
            logger.warning(f"Action outside vocabulary: {act['name']!r} "
                           f"({rec.get('assay_name')!r})")
    if token_logprobs:
        attach_assay_logprobs(token_logprobs, assays)
    result = {"assays": assays}
    if token_logprobs:
        rl = response_logprob(token_logprobs)
        if rl:
            result["_response_logprob"] = rl
        result["_logprob_tokens"] = token_logprobs
        result["_logprob_content"] = content
    return result


# ---------------------------------------------------------------------------
# Text preparation
# ---------------------------------------------------------------------------

def _clean_html(text: str) -> str:
    try:
        from tasks.utils import clean_html
        return clean_html(text)
    except ImportError:
        import re
        return re.sub(r"<[^>]+>", " ", text).replace("\xa0", " ").strip()


def build_ke_text(ke) -> tuple[str, str]:
    """Returns (prompt_text, source_field) where source_field is the KE field the
    assay text was drawn from: 'measurement_methodology' or 'description'."""
    parts = []
    title = getattr(ke, "short_name", None) or getattr(ke, "title", None) or ""
    if title:
        parts.append(f"KE title: {title}")
    mm = getattr(ke, "measurement_methodology", None) or ""
    if mm and mm.strip():
        parts.append(f"Measurement methodology:\n{_clean_html(mm)}")
        source_field = "measurement_methodology"
    else:
        source_field = "description"
        desc = getattr(ke, "description", None) or ""
        if desc and desc.strip():
            parts.append("Description (methodology not separately recorded):\n"
                         + _clean_html(desc)[:2000])
    return "\n\n".join(parts), source_field


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _atomic_write_json(path, obj) -> None:
    tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _pop_tokens_to_sidecar(sidecar_f, ke_id: str, result: dict) -> None:
    """Stream raw token logprobs to the sidecar, plus prompt length / token usage
    (prompt_chars always; prompt_tokens etc. when the backend returns `usage`). A line
    is written even with logprobs off, so usage stats cover every model."""
    tokens = result.pop("_logprob_tokens", None)
    content = result.pop("_logprob_content", None)
    usage = result.pop("_usage", None)
    if tokens is None and usage is None:
        return
    line = {"ke_id": ke_id, "content": content, "tokens": tokens}
    if usage:
        line.update(usage)
    sidecar_f.write(json.dumps(line, ensure_ascii=False) + "\n")
    sidecar_f.flush()


# ---------------------------------------------------------------------------
# Ploomber task body
# ---------------------------------------------------------------------------

def _run(client: ChatLLMClient) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from aopkb import Data
    from tasks.utils import preprocess_timestamps

    with open(upstream["parse_pydantic"]["json"], encoding="utf-8") as f:
        json_data = json.load(f)
    preprocess_timestamps(json_data)
    data = Data.model_validate(json_data)

    assays_path = product["json"]
    sidecar = Path(assays_path).with_name("assays_logprobs.jsonl")
    errors_path = Path(assays_path).with_name("assays_errors.json")

    # KE selection — same ke_filter as extract_kec (OECD status / explicit ids / limit)
    allowed_uuids, ke_ids_set, _limit = ke_filter_uuids(data, ke_filter)
    wiki_map = build_wiki_id_map(data)   # uuid -> wiki-id; keys are wiki-id end-to-end
    if allowed_uuids is not None:
        logger.info(f"OECD filter → {len(allowed_uuids)} KE UUIDs")

    def _passes(ke_id: str) -> bool:
        if allowed_uuids is not None and ke_id not in allowed_uuids:
            return False
        if ke_ids_set is not None:
            wid = wiki_map.get(ke_id, ke_id)
            if ke_id not in ke_ids_set and wid not in ke_ids_set:
                return False
        return True

    # resume: load existing per-KE results
    all_assays: dict = {}
    try:
        with open(assays_path, encoding="utf-8") as f:
            all_assays = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # "done" = key present (even an empty result), so a KE that legitimately yields
    # zero assays is terminal and not re-queued every resume. Errors are NOT stored
    # (see below), so they remain absent and get retried. To re-process the legacy
    # empties (KEs that now have a usable description), clear empty entries once:
    #   jq 'map_values(select(length>0))' assays.json   (or just delete [] entries)
    done = set(all_assays)
    if done:
        logger.info(f"Resuming: {len(done)} KEs already processed, skipping")

    # durable per-KE error record (errored KEs stay absent from assays.json so they
    # retry; this file records why each failed and is pruned as they succeed)
    errors: dict = {}
    try:
        with open(errors_path, encoding="utf-8") as f:
            errors = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if errors:
        logger.info(f"{len(errors)} KEs errored previously — will retry")

    # build work list (filtered KEs with text, not already processed).
    # Keyed by wiki-id (e.g. "KE142") to match the on-disk assays.json so resume
    # actually skips done KEs and writes don't duplicate under raw UUIDs.
    work = []
    n_skip = n_filtered = 0
    for ke in data.key_event:
        uuid = str(ke.id)
        wiki_id = wiki_map.get(uuid, uuid)
        if not _passes(uuid):
            n_filtered += 1
            continue
        if wiki_id in done:
            continue
        mm = getattr(ke, "measurement_methodology", None) or ""
        desc = getattr(ke, "description", None) or ""
        if not mm.strip() and not desc.strip():
            n_skip += 1                       # no text → skip, store no placeholder
            continue
        if _limit and len(work) >= int(_limit):
            break
        text, source_field = build_ke_text(ke)
        work.append((wiki_id, text, source_field))

    n_done_before = len(done)
    n_total = n_done_before + len(work)
    logger.info(f"Queued {len(work)} new KEs (already done {n_done_before}, "
                f"no-text {n_skip}, filtered-out {n_filtered}), "
                f"total={n_total}, threads={threads}")

    FLUSH_EVERY = max(1, int(flush_every or 20))
    logger.info(f"checkpoint cadence: flush assays.json every {FLUSH_EVERY} KEs")
    state = {"since": 0, "err": 0, "n_assays": 0}
    sidecar_f = open(sidecar, "a", encoding="utf-8")

    def _submit(ke_id, text, source_field):
        user = f"Key Event measurement methodology:\n\n{text}"
        return ke_id, source_field, client.complete(SYSTEM_PROMPT, user, parse_assays)

    try:
        with ThreadPoolExecutor(max_workers=int(threads or 1)) as pool:
            futures = {pool.submit(_submit, kid, txt, src): kid
                       for kid, txt, src in work}
            for i, fut in enumerate(as_completed(futures), n_done_before + 1):
                ke_id, source_field, result = fut.result()
                if result.get("error"):
                    state["err"] += 1
                    # key stays absent from assays.json (so it retries); record why
                    errors[ke_id] = {"error": str(result["error"]),
                                     "source_field": source_field,
                                     "ts": datetime.now().isoformat(timespec="seconds")}
                    logger.info(f"[{i}/{n_total}] {ke_id} → error ({result['error']})")
                else:
                    _pop_tokens_to_sidecar(sidecar_f, ke_id, result)
                    recs = result.get("assays") or []
                    for r in recs:                  # stamp provenance on each assay
                        r["source_field"] = source_field
                    all_assays[ke_id] = recs
                    errors.pop(ke_id, None)         # succeeded now — clear any old error
                    state["n_assays"] += len(recs)
                    logger.info(f"[{i}/{n_total}] {ke_id} → ok ({len(recs)} assays: "
                                f"{[r.get('assay_name') for r in recs]})")
                state["since"] += 1
                if state["since"] >= FLUSH_EVERY:
                    _atomic_write_json(assays_path, all_assays)
                    _atomic_write_json(errors_path, errors)
                    state["since"] = 0
    finally:
        sidecar_f.close()
        _atomic_write_json(assays_path, all_assays)
        _atomic_write_json(errors_path, errors)

    logger.info(f"Done. assays={state['n_assays']}, errors={state['err']} "
                f"(see {errors_path.name}), KEs in output={len(all_assays)}")


# ---------------------------------------------------------------------------
# Entry point (Ploomber)
# ---------------------------------------------------------------------------

Path(product["json"]).parent.mkdir(parents=True, exist_ok=True)

if enabled:
    # from_config picks up base_url / api_key / extra_body / referer / title (incl.
    # OpenRouter). max_tokens (65536) now matches from_config's own default, so passing
    # it is redundant — kept explicit to pin this task's value; the rest already match.
    with ChatLLMClient.from_config(
        models[model_key],
        logger=logger,
        max_tokens=int(max_tokens or 65536),
    ) as _client:
        logger.info(f"{_client.endpoint}  {_client.model}")
        _t0 = time.perf_counter()
        _run(_client)
        logger.info(f"Elapsed: {time.perf_counter() - _t0:.1f}s")
else:
    logger.info("extract_assays disabled — skipping")
