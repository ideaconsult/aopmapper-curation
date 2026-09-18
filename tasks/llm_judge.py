"""
llm_judge.py — OpenAI-compatible LLM-as-judge client.  LIBRARY, not a task (import-safe).

Ported from the standalone `assess_kec_triples.py` into a reusable, configurable client
with two judge tasks:

  - judge_triple(title, description, level, triple) -> {verdict, score, reasoning}
      "Is this (object, process, action) triple correct for this Key Event?"
      Used by the ground-truth label-noise audit (spec §4.5).

  - judge_slot(name_a, name_b) -> Optional[bool]
      "Do these two biomedical terms refer to the same entity/concept?"
      Used by the hybrid matcher's judge tier (tasks/kec_compare.py) for borderline
      embedding cases.

Config comes from the project `models[model_key]` dict (base_url / model_llm / api_key),
so the judge can be pointed at a *different* model than the extractor — important because a
same-family judge shares the extractor's blind spots (spec §8.1).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

VERDICTS = {"correct", "partial", "wrong"}

TRIPLE_SYSTEM_PROMPT = """You are an expert in molecular toxicology and biological ontologies.
Your task is to assess whether a biological event triple (object, process, action)
correctly describes what happens in a Key Event based on its textual description.

A triple has three optional slots:
  - biological object:  the cellular/molecular entity involved (e.g. mitochondria, DNA)
  - biological process: the biological process occurring (e.g. apoptosis, oxidative stress)
  - biological action:  the direction or type of change (e.g. increased, decreased, activated)

Respond ONLY with a JSON object — no markdown, no explanation outside the JSON:
{
  "verdict": "correct" | "partial" | "wrong",
  "score": <float 0.0 to 1.0>,
  "reasoning": "<one or two sentences explaining the verdict>"
}

Verdict definitions:
  correct  — the triple accurately and specifically captures the KE biology
  partial  — the triple is related but imprecise, too general, or missing a key aspect
  wrong    — the triple does not match the KE description or is biologically incorrect

Score: 1.0 = fully correct, 0.5 = partially correct, 0.0 = wrong."""

SLOT_SYSTEM_PROMPT = """You are an expert in biomedical ontologies. Decide whether two terms
refer to the SAME biological entity or concept (synonyms, lexical variants, or the same concept
at the same level of granularity count as the same; a parent/child or merely related concept does NOT).

Respond ONLY with JSON — no markdown:
{"equivalent": true | false, "reasoning": "<short>"}"""


def _fmt_slot(t: Optional[dict]) -> str:
    if not t:
        return "(absent)"
    return f"{t.get('name', '?')} [{t.get('iri', 'no IRI')}]"


def build_triple_message(title: str, description: str, level: str, triple: dict) -> str:
    desc = (description or title or "").strip()
    if len(desc) > 2000:
        desc = desc[:2000] + "..."
    return (
        f"KEY EVENT\n"
        f"Title: {title}\n"
        f"Biological level: {level}\n\n"
        f"Description:\n{desc}\n\n"
        f"TRIPLE TO ASSESS:\n"
        f"  biological object:  {_fmt_slot(triple.get('object'))}\n"
        f"  biological process: {_fmt_slot(triple.get('process'))}\n"
        f"  biological action:  {_fmt_slot(triple.get('action'))}\n\n"
        f"Is this triple correct, partial, or wrong for this Key Event?"
    )


class JudgeClient:
    """OpenAI-compatible /chat/completions judge. No LangChain dependency."""

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = .0, max_retries: int = 3,
                 max_tokens: float = 65536,
                 retry_delay: float = 3.0):
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    def _call(self, system: str, user: str, max_tokens: int = 512) -> str:
        import httpx
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
        last = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as c:
                    r = c.post(self.endpoint, json=payload, headers=self.headers)
                    r.raise_for_status()
                    try:
                        body = r.json()
                    except Exception as je:
                        raise ValueError(
                            f"Non-JSON response (status {r.status_code}, "
                            f"{len(r.content)} bytes): {r.text[:200]!r}"
                        ) from je
                    msg = body["choices"][0]["message"]
                    content = (msg.get("content") or "").strip()
                    # strip inline <think>...</think> if reasoning_content is absent
                    if not msg.get("reasoning_content"):
                        content = re.sub(r"<think>.*?</think>", "", content,
                                         flags=re.DOTALL).strip()
                    if not content:
                        raise ValueError(
                            f"Empty content (finish_reason="
                            f"{body['choices'][0].get('finish_reason')!r})"
                        )
                    return content
            except Exception as e:
                last = e
                logger.warning(f"judge attempt {attempt}/{self.max_retries}: {e}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * attempt)
        raise RuntimeError(f"judge call failed: {last}")

    @staticmethod
    def _parse_json(raw: str) -> dict:
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        # Strip any leading non-JSON preamble (e.g. diffusion model <channel|> tokens)
        first = min((raw.find(c) for c in "{[" if raw.find(c) != -1), default=-1)
        if first > 0:
            raw = raw[first:]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Truncate at last } and retry (handles trailing garbage/truncated output)
        last_brace = raw.rfind("}")
        if last_brace > 0:
            try:
                return json.loads(raw[:last_brace + 1])
            except json.JSONDecodeError:
                pass
        # Last resort: extract individual fields with regex
        result = {}
        m = re.search(r'"verdict"\s*:\s*"(\w+)"', raw)
        if m:
            result["verdict"] = m.group(1)
        m = re.search(r'"score"\s*:\s*([0-9.]+)', raw)
        if m:
            result["score"] = float(m.group(1))
        m = re.search(r'"reasoning"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if m:
            result["reasoning"] = m.group(1)
        if result:
            return result
        raise ValueError(f"unparseable judge response: {raw[:200]!r}")

    def judge_triple(self, title: str, description: str, level: str, triple: dict) -> dict:
        """Return {verdict, score, reasoning}; verdict='error' on failure."""
        user = build_triple_message(title, description, level, triple)
        try:
            p = self._parse_json(self._call(TRIPLE_SYSTEM_PROMPT, user))
            verdict = str(p.get("verdict", "")).lower()
            if verdict not in VERDICTS:
                verdict = "partial"
            return {
                "verdict": verdict,
                "score": float(p.get("score", 0.5)),
                "reasoning": str(p.get("reasoning", "")),
            }
        except Exception as e:
            return {"verdict": "error", "score": None, "reasoning": str(e)[:200]}

    def judge_slot(self, name_a: str, name_b: str) -> Optional[bool]:
        """True/False if the two terms are the same entity; None on failure."""
        user = (f"Term A: {name_a}\nTerm B: {name_b}\n"
                f"Do they refer to the same biological entity/concept?")
        try:
            p = self._parse_json(self._call(SLOT_SYSTEM_PROMPT, user, max_tokens=256))
            return bool(p.get("equivalent"))
        except Exception as e:
            logger.warning(f"judge_slot failed: {e}")
            return None


def judge_client_from_model(models: dict, model_key: str, timeout: float = 60.0) -> JudgeClient:
    """Build a JudgeClient from a project `models[model_key]` entry.
    `api_key` in the config is the NAME of an env var (resolved here)."""
    m = models[model_key]
    api_env = m.get("api_key")
    api_key = os.environ.get(api_env, "ollama") if api_env else "ollama"
    return JudgeClient(base_url=m.get("base_url", "http://192.168.0.252:8000/v1"),
                       api_key=api_key,
                       model=m["model_llm"], 
                       max_tokens=m.get("max_tokens", 65536),
                       timeout=m.get("timeout",7200),
                       max_retries=int(m.get("max_retries", 1)),
                       retry_delay=float(m.get("retry_delay", 2.0))
                       )
