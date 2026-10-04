"""OpenRouter chat client that returns parsed JSON.

  POST https://openrouter.ai/api/v1/chat/completions   (OpenAI-compatible)

Environment: OPENROUTER_API_KEY (required for real calls), OPENROUTER_MODEL (default
stealth/space-bunny-alpha, free, 1M context), OPENROUTER_REASONING (reasoning effort:
low (default) | medium | high | none). Reasoning tokens count against max_tokens: a reply
cut off by the limit (finish_reason "length") raises LlmTruncated instead of being
retried as is, so callers can send less per call. Responses are cached in the literature cache
(data/literature-cache/cache.sqlite, keyed by model + messages, not the key), so a rerun
with the same papers and prompts costs no calls; Cache(refresh=True) ignores it.

Many workers (threads) share one Llm and run calls in parallel. Rate limits and transient
failures (429, 5xx, network errors, upstream errors in a 200 body) are retried forever
with exponential backoff, 1 s doubling up to MAX_WAIT (30 s), with jitter (Retry-After
when the server sends a shorter one). The backoff is shared: one 429 makes every worker
pause until the cooldown ends, so they do not keep hitting the limit, and a success
resets it. Only errors that a retry cannot fix (401, 400, ...) and replies cut off at
max_tokens raise. An empty reply is retried EMPTY_RETRIES times; a reply that is not
valid JSON once with a "JSON only" reminder.
"""
import json
import os
import random
import re
import sys
import threading
import time

import requests
from literature.base import Cache

API = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "stealth/space-bunny-alpha"
TIMEOUT = 300
MAX_WAIT = 30.0  # backoff cap, seconds
EMPTY_RETRIES = 3
TRANSIENT = (408, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529)


class LlmError(RuntimeError):
    pass


class LlmTruncated(LlmError):
    """The reply hit max_tokens (reasoning included); retrying the same call won't help."""


def parse_json(text: str):
    """JSON from a model reply: strips ``` fences and text around the outer object."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


class Llm:
    def __init__(self, cache: Cache, model: str | None = None, temperature: float = 0.1,
                 reasoning: str | None = None):
        self.cache = cache
        self.model = (model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL).strip()
        self.reasoning = (reasoning or os.environ.get("OPENROUTER_REASONING") or "low").strip()
        self.key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        self.temperature = temperature
        self.session = requests.Session()
        self.stats = {"calls": 0, "cached": 0, "errors": 0, "prompt_tokens": 0,
                      "completion_tokens": 0, "seconds": 0.0, "retries": 0}
        self._lock = threading.Lock()
        self._cooldown_until = 0.0  # shared backoff: no worker sends before this
        self._failures = 0  # consecutive rate-limit / transient failures (all workers)
        self._last_backoff = 0.0  # when the current backoff step was taken

    def _wait_turn(self):
        while (delay := self._cooldown_until - time.monotonic()) > 0:
            time.sleep(delay)

    def _backoff(self, why: str, sent: float, retry_after: str | None = None):
        """Register a transient failure of a request sent at `sent` (monotonic): every
        worker pauses for the next backoff step. Requests that were already in flight when
        the current step was taken belong to the same wave and do not escalate it."""
        with self._lock:
            if sent < self._last_backoff:
                return
            self._last_backoff = time.monotonic()
            self._failures += 1
            self.stats["retries"] += 1
            wait = min(MAX_WAIT, 2 ** (self._failures - 1))
            try:
                wait = min(wait, float(retry_after)) if retry_after else wait
            except ValueError:
                pass
            wait = max(0.5, wait) * random.uniform(0.8, 1.0)
            until = time.monotonic() + wait
            if until > self._cooldown_until:  # concurrent failures don't stack
                self._cooldown_until = until
                print(f"  ! llm {why[:100]}; all workers pause {wait:.1f}s",
                      file=sys.stderr, flush=True)

    def _success(self):
        with self._lock:
            self._failures = 0

    def _count(self, **kw):
        with self._lock:
            for k, v in kw.items():
                self.stats[k] += v

    def chat(self, system: str, user: str, max_tokens: int = 32000):
        """Parsed JSON reply (a dict). Raises LlmError (LlmTruncated when cut off)."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        body = {"model": self.model, "messages": messages, "temperature": self.temperature,
                "max_tokens": max_tokens, "response_format": {"type": "json_object"}}
        if self.reasoning != "none":
            body["reasoning"] = {"effort": self.reasoning, "exclude": True}
        k = Cache.key("POST", API, None, body)
        cached = self.cache.get(k)
        if cached is not None:
            try:
                out = parse_json(cached)
                self._count(cached=1)
                return out
            except (json.JSONDecodeError, ValueError):
                pass
        if not self.key:
            raise LlmError("OPENROUTER_API_KEY is not set")
        text = self._post(body)
        try:
            out = parse_json(text)
        except (json.JSONDecodeError, ValueError):
            retry = {**body, "messages": [*messages, {"role": "assistant", "content": text},
                                          {"role": "user", "content":
                                           "That was not valid JSON. Return the complete "
                                           "answer again as one valid JSON object only."}]}
            text = self._post(retry)
            try:
                out = parse_json(text)
            except (json.JSONDecodeError, ValueError) as e:
                self._count(errors=1)
                raise LlmError(f"invalid JSON from model: {text[:200]!r}") from e
        self.cache.put(k, API, json.dumps(out, ensure_ascii=False))
        return out

    def _post(self, body: dict) -> str:
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                   "HTTP-Referer": "https://github.com/justmycode37/garra-rufa",
                   "X-Title": "garra-rufa evidence graph"}
        empty = 0
        while True:  # transient failures are retried forever (see module docstring)
            self._wait_turn()
            sent, t0 = time.monotonic(), time.time()
            try:
                r = self.session.post(API, json=body, headers=headers, timeout=TIMEOUT)
            except requests.RequestException as e:
                self._backoff(f"{type(e).__name__} {e}", sent)
                continue
            self._count(seconds=time.time() - t0)
            if r.status_code in TRANSIENT:
                self._backoff(f"HTTP {r.status_code} {r.text[:120]}", sent,
                              r.headers.get("Retry-After"))
                continue
            if not r.ok:
                self._count(errors=1)
                raise LlmError(f"HTTP {r.status_code}: {r.text[:400]}")
            try:
                d = r.json()
            except ValueError:
                self._backoff(f"non-JSON response {r.text[:80]!r}", sent)
                continue
            if d.get("error"):  # OpenRouter reports upstream errors (often 429) in a 200 body
                err = d["error"] if isinstance(d["error"], dict) else {"message": d["error"]}
                code = err.get("code")
                if isinstance(code, int) and code not in TRANSIENT and 400 <= code < 500:
                    self._count(errors=1)
                    raise LlmError(f"upstream error {str(err)[:400]}")
                self._backoff(f"upstream error {str(err)[:120]}", sent)
                continue
            self._success()
            u = d.get("usage") or {}
            self._count(calls=1, prompt_tokens=u.get("prompt_tokens") or 0,
                        completion_tokens=u.get("completion_tokens") or 0)
            choice = (d.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            if choice.get("finish_reason") == "length":
                self._count(errors=1)
                raise LlmTruncated(f"reply cut off at max_tokens {body['max_tokens']} "
                                   f"({u.get('completion_tokens')} completion tokens, "
                                   f"{len(content)} chars of answer)")
            if not content.strip():
                empty += 1
                if empty > EMPTY_RETRIES:
                    self._count(errors=1)
                    raise LlmError(f"{empty} empty replies (finish_reason "
                                   f"{choice.get('finish_reason')})")
                continue
            return content
