"""
llm_logprobs.py — shared token→field log-probability alignment.

OpenAI-compatible chat endpoints can return per-token log-probabilities
(`logprobs`/`top_logprobs`). To turn those into a per-field trust signal we align
the response tokens back to the character spans of each extracted value and
aggregate. This module holds the generic, schema-agnostic primitives so both the
KEC and the assay extractors can share one implementation.

Primitives (proven in extract_kec.py):
  token_offsets(tokens)            -> [(start, end, logprob, top_logprobs), ...]
  span_logprobs(offsets, s, e)     -> [logprob, ...] for tokens overlapping [s, e)
  first_token_top(offsets, s, e)   -> top_logprobs of the first overlapping token

SpanAligner is a small stateful helper: it reconstructs the response string from
the tokens and exposes `annotate(value, categorical=...)`, advancing a cursor so
repeated values are matched left-to-right (same behaviour as extract_kec's
attach_logprobs). For a *categorical* field (a controlled vocabulary, e.g. the KEC
action or the assay endpoint_direction) it also returns `top_logprobs`, whose
rank1−rank2 gap is a direct classification-confidence margin.

response_logprob(tokens) gives a coarse whole-answer aggregate (mean, perplexity).
"""
from __future__ import annotations

import math


def token_offsets(tokens: list) -> list:
    """[(start, end, logprob, top_logprobs), ...] with char offsets into the
    token-reconstructed string."""
    offs = []
    pos = 0
    for t in tokens:
        tok = t.get("token") or ""
        lp = t.get("logprob")
        lp = float(lp) if lp is not None else None
        offs.append((pos, pos + len(tok), lp, t.get("top_logprobs")))
        pos += len(tok)
    return offs


def span_logprobs(offsets: list, start: int, end: int) -> list:
    """Logprobs of all tokens overlapping the char span [start, end), nulls excluded."""
    return [lp for (s, e, lp, _top) in offsets
            if s < end and e > start and lp is not None]


def first_token_top(offsets: list, start: int, end: int):
    """top_logprobs of the first token overlapping the span (classification margin)."""
    for (s, e, _lp, top) in offsets:
        if s < end and e > start:
            return top
    return None


class SpanAligner:
    """Align extracted values back to response-token spans, left to right.

    Usage:
        al = SpanAligner(token_logprobs)
        agg = al.annotate("increased", categorical=True)  # {logprob_mean, ..., top_logprobs}
        term.update(agg or {})
    """

    def __init__(self, tokens: list):
        self.recon = "".join(t.get("token") or "" for t in (tokens or []))
        self.offsets = token_offsets(tokens or [])
        self.cursor = 0

    def annotate(self, value: str | None, *, categorical: bool = False) -> dict | None:
        """Return {logprob_mean, logprob_min[, top_logprobs]} for `value`'s span and
        advance the cursor past it. None if the value can't be located."""
        if not value:
            return None
        idx = self.recon.find(value, self.cursor)
        if idx < 0:                          # duplicate / out-of-order fallback
            idx = self.recon.find(value)
        if idx < 0:
            return None
        start, end = idx, idx + len(value)
        self.cursor = end
        out: dict = {}
        lps = span_logprobs(self.offsets, start, end)
        if lps:
            out["logprob_mean"] = sum(lps) / len(lps)
            out["logprob_min"] = min(lps)
        if categorical:
            top = first_token_top(self.offsets, start, end)
            if top:
                out["top_logprobs"] = top
        return out or None


def response_logprob(tokens: list) -> dict | None:
    """Coarse whole-answer aggregate comparable across models."""
    all_lps = [t.get("logprob") for t in (tokens or [])
               if t.get("logprob") is not None]
    if not all_lps:
        return None
    mean = sum(all_lps) / len(all_lps)
    return {"mean": mean, "perplexity": math.exp(-mean), "n_tokens": len(all_lps)}


def margin(top_logprobs: list | None) -> float | None:
    """rank1 − rank2 of a categorical field's top_logprobs (≥0; larger = more confident)."""
    if top_logprobs and len(top_logprobs) >= 2:
        return top_logprobs[0]["logprob"] - top_logprobs[1]["logprob"]
    return None
