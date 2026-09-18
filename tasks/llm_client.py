"""
llm_client.py — shared OpenAI-compatible chat client + JSON-block parser. LIBRARY.

Import-safe (no module-level side effects), so both extract_kec.py and extract_assays.py
can reuse one implementation of the network/retry/reasoning/JSON-extraction logic. The
schema-specific bits (pydantic validation, per-field logprob attachment) stay in each
extractor and are supplied to `complete()` as a `parser` callback so that a JSON parse
failure still triggers a retry.

  client = ChatLLMClient(base_url, api_key, model, request_logprobs=True, top_logprobs=5)
  result = client.complete(SYSTEM_PROMPT, user_text, parser=my_parser)
      # parser(content: str, tokens: list|None, strip_think: bool) -> dict
      # → returns parser's dict, or {"error": str} after exhausting retries

parse_json_block(content, strip_think) handles code fences, <think> blocks, leading
preamble and the "Extra data" trailing-content case, returning the raw parsed object.

OpenRouter / gateway support
----------------------------
Hosted gateways (OpenRouter especially) differ from a plain vLLM endpoint in ways a
raw httpx client must handle:
  * an HTTP 200 can carry an *error* body with NO `choices` (provider/routing failure);
    we surface it as a retryable error instead of KeyError-ing on choices[0];
  * provider/thinking knobs (presence_penalty, top_p, chat_template_kwargs, thinking
    budgets, provider routing) ride in `extra_body`, merged into the request payload;
  * OpenRouter likes attribution headers (HTTP-Referer / X-Title);
  * 429 / 5xx / "Provider returned error" are transient → exponential backoff.
Pass these via `extra_body=`, `referer=`, `title=`; or build straight from a per-model
config dict with `ChatLLMClient.from_config(cfg, logger=...)`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable, Optional

import httpx

_log = logging.getLogger(__name__)


def parse_json_block(content: str, strip_think: bool = True):
    """Strip fences / <think> / preamble and parse the first JSON object or array."""
    json_str = content
    if strip_think:
        json_str = re.sub(r"<think>.*?</think>", "", json_str, flags=re.DOTALL).strip()
    if json_str.startswith("```"):
        json_str = "\n".join(
            ln for ln in json_str.splitlines()
            if not ln.strip().startswith("```")).strip()
    first = min((json_str.find(c) for c in "{[" if json_str.find(c) != -1), default=-1)
    if first > 0:
        json_str = json_str[first:]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        # "Extra data": valid JSON followed by trailing content — truncate at error pos
        if "Extra data" in str(e) and e.pos > 0:
            return json.loads(json_str[:e.pos])
        raise ValueError(
            f"JSON parse failed ({e}); response was: {json_str[:300]!r}") from e


_TRANSIENT_MARKERS = (
    "429", "500", "502", "503", "504",
    "provider returned error", "rate limit", "overloaded", "timeout",
    "apiconnectionerror", "unsupportedprotocol", "temporarily",
)


def _is_transient(exc: Exception) -> bool:
    """OpenRouter/gateway failures worth retrying (rate limit, provider routing,
    upstream 5xx, connection blips). JSON decode truncation is transient too."""
    if isinstance(exc, json.JSONDecodeError):
        return True
    s = str(exc).lower()
    return any(m in s for m in _TRANSIENT_MARKERS)


class ChatLLMClient:
    """OpenAI-compatible /v1/chat/completions client (no LangChain).

    Works against a plain vLLM endpoint or a hosted gateway (OpenRouter). See the
    module docstring for the gateway-specific handling (`extra_body`, attribution
    headers, error-in-200 body, transient backoff)."""

    def __init__(self, base_url: str, api_key: str, model: str, *,
                 timeout: float = 7200.0, max_retries: int = 1, retry_delay: float = 2.0,
                 max_tokens: int = 65536, temperature: float = 0.1,
                 request_logprobs: bool = True, top_logprobs: int = 3,
                 extra_body: Optional[dict] = None,
                 referer: Optional[str] = None, title: Optional[str] = None,
                 logger: Optional[logging.Logger] = None):
        base_url = (base_url or "").strip() or "https://openrouter.ai/api/v1"
        if not base_url.startswith(("http://", "https://")):
            base_url = f"https://{base_url}"          # tolerate a bare host
        self.base_url = base_url.rstrip("/")
        self.endpoint = f"{self.base_url}/chat/completions"
        self.is_openrouter = "openrouter.ai" in self.base_url
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.request_logprobs = request_logprobs
        self.top_logprobs = top_logprobs
        self.extra_body = dict(extra_body) if extra_body else {}
        self.log = logger or _log
        self.headers = {"Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}"}
        # OpenRouter uses these for attribution/ranking; harmless elsewhere.
        if referer:
            self.headers["HTTP-Referer"] = referer
        if title:
            self.headers["X-Title"] = title
        # Shared pool: reuses keep-alive connections across threads. max_connections=None
        # is unlimited so high thread counts (e.g. 128) aren't throttled by the pool.
        # max_keepalive_connections caps idle sockets after bursts finish.
        self._http = httpx.Client(
            timeout=self.timeout,
            limits=httpx.Limits(max_connections=None, max_keepalive_connections=32),
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._http.close()

    @classmethod
    def from_config(cls, cfg: dict, *, logger: Optional[logging.Logger] = None,
                    **overrides) -> "ChatLLMClient":
        """Build a client from a per-model config dict (the `models[model_key]` entry).

        Recognised keys: model_llm, base_url, api_key (name of an *env var* holding the
        secret), timeout, max_tokens, temperature, logprobs, top_logprobs, extra_body,
        referer/http_referer, title/x_title. `**overrides` win over the config.
        """
        import os
        log = logger or _log
        api_key_name = cfg.get("api_key") or ""
        api_key = (os.environ.get(api_key_name, "") or "").strip()
        if api_key_name and not api_key:
            # Named env var is unset/empty in this process — the usual cause of a 401
            # on a remote endpoint (e.g. set at User scope after the IDE launched, so
            # the process never inherited it). Warn loudly instead of silently sending
            # `Bearer ollama`.
            log.warning(f"api_key env var {api_key_name!r} is empty/unset in this "
                        f"process — using no-auth fallback; remote calls will 401")
        elif api_key_name:
            import hashlib
            fp = hashlib.sha256(api_key.encode()).hexdigest()[:8]
            # OpenRouter keys all share the sk-or-v1- prefix, so a prefix is useless
            # to tell keys apart; a sha256 fingerprint + tail distinguishes them
            # without leaking the secret.
            log.info(f"auth: {api_key_name} resolved (len={len(api_key)}, "
                     f"fp={fp}, …{api_key[-4:]})")
        if not api_key:
            api_key = "ollama"          # local/no-auth fallback (e.g. ollama/vLLM)
        params = dict(
            base_url=cfg.get(
                "base_url", os.environ.get("AOP4ENM_LLM_BASE_URL", "http://localhost:8000/v1")),
            api_key=api_key,
            model=cfg.get("model_llm") or "llama3.2",
            timeout=float(cfg.get("timeout", 7200.0)),
            max_retries=int(cfg.get("max_retries", 1)),
            retry_delay=float(cfg.get("retry_delay", 2.0)),
            max_tokens=int(cfg.get("max_tokens", 65536) or 65536),
            temperature=float(cfg.get("temperature", 0.1)),
            request_logprobs=bool(cfg.get("logprobs", True)),
            top_logprobs=int(cfg.get("top_logprobs", 3) or 3),
            extra_body=cfg.get("extra_body"),
            referer=cfg.get("referer") or cfg.get("http_referer"),
            title=cfg.get("title") or cfg.get("x_title"),
            logger=logger,
        )
        params.update(overrides)
        return cls(**params)

    def complete(self, system_prompt: str, user_content: str,
                 parser: Callable[[str, Optional[list], bool], dict]) -> dict:
        """POST one chat request and hand the response to `parser`.

        parser(content, token_logprobs, strip_think) -> dict. Any exception (network,
        empty response, JSON/validation error) is retried up to max_retries; the final
        failure returns {"error": str}. Transient gateway errors back off exponentially.
        """
        import httpx
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.request_logprobs:
            payload["logprobs"] = True
            payload["top_logprobs"] = self.top_logprobs
        if self.extra_body:                       # provider/thinking/routing knobs
            payload.update(self.extra_body)

        prompt_chars = len(system_prompt or "") + len(user_content or "")

        def _with_usage(result, data):
            """Stash prompt length + server token usage on the parsed result, so the
            extractor can record it (chars known locally; tokens from the API `usage`)."""
            if isinstance(result, dict) and "_usage" not in result:
                u = (data.get("usage") or {}) if isinstance(data, dict) else {}
                result["_usage"] = {
                    "prompt_chars": prompt_chars,
                    "prompt_tokens": u.get("prompt_tokens"),
                    "completion_tokens": u.get("completion_tokens"),
                    "total_tokens": u.get("total_tokens"),
                }
            return result

        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self._http.post(self.endpoint, json=payload, headers=self.headers)
                if not r.is_success:
                    raise ValueError(f"HTTP {r.status_code}: {r.text[:400]!r}")
                data = r.json()
                # OpenRouter/gateways can return 200 with an error body and no
                # choices (provider/routing failure) — surface it, don't KeyError.
                if isinstance(data, dict) and data.get("error") and not data.get("choices"):
                    raise ValueError(f"Provider returned error: {data['error']}")
                choices = data.get("choices") if isinstance(data, dict) else None
                if not choices:
                    raise ValueError(
                        f"No choices returned (body keys={list(data)[:6]})")
                choice = choices[0]
                msg = choice.get("message") or {}
                content = (msg.get("content") or "").strip()
                reasoning = (msg.get("reasoning_content")
                             or msg.get("reasoning") or "").strip()
                if not content and reasoning:
                    # Whole answer routed into the reasoning field (reasoning parser
                    # misfiring on a non-thinking model). Fall back; no content logprobs.
                    return _with_usage(parser(reasoning, None, True), data)
                if not content:
                    raise ValueError(
                        f"Empty content (finish_reason="
                        f"{choice.get('finish_reason')!r}, reasoning_len=0)")
                lp = (choice.get("logprobs") or {}).get("content")
                return _with_usage(parser(content, lp, not bool(reasoning)), data)
            except Exception as e:
                last_err = e
                self.log.warning(f"Attempt {attempt}/{self.max_retries}: {e}")
                if "HTTP 400" in str(e):
                    break  # permanent client/config error — no point retrying
                if attempt < self.max_retries:
                    # exponential backoff for transient gateway errors, linear otherwise
                    delay = (self.retry_delay * (2 ** (attempt - 1))
                             if _is_transient(e) else self.retry_delay * attempt)
                    time.sleep(delay)
        return {"error": str(last_err)}
