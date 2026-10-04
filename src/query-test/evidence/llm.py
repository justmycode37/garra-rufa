"""OpenRouter chat client that returns parsed JSON.

  POST https://openrouter.ai/api/v1/chat/completions   (OpenAI-compatible)

Environment: OPENROUTER_API_KEY (required for real calls), OPENROUTER_MODEL (default
stealth/space-bunny-alpha, free, 1M context). Responses are cached in the literature cache
(data/literature-cache/cache.sqlite, keyed by model + messages, not the key), so a rerun
with the same papers and prompts costs no calls; Cache(refresh=True) ignores it.

Free models are rate limited: 429 / 5xx are retried with backoff (Retry-After when given).
A reply that is not valid JSON is retried once with a "JSON only" reminder.
"""
import json
import os
import re
import sys
import threading
import time

import requests
from literature.base import Cache

API = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "stealth/space-bunny-alpha"
TIMEOUT = 300
RETRIES = 6


class LlmError(RuntimeError):
    pass


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
    def __init__(self, cache: Cache, model: str | None = None, temperature: float = 0.1):
        self.cache = cache
        self.model = (model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL).strip()
        self.key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        self.temperature = temperature
        self.session = requests.Session()
        self.stats = {"calls": 0, "cached": 0, "errors": 0, "prompt_tokens": 0,
                      "completion_tokens": 0, "seconds": 0.0}
        self._lock = threading.Lock()

    def _count(self, **kw):
        with self._lock:
            for k, v in kw.items():
                self.stats[k] += v

    def chat(self, system: str, user: str, max_tokens: int = 16000):
        """Parsed JSON reply (a dict). Raises LlmError."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        body = {"model": self.model, "messages": messages, "temperature": self.temperature,
                "max_tokens": max_tokens, "response_format": {"type": "json_object"}}
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
        last = ""
        for attempt in range(RETRIES + 1):
            t0 = time.time()
            try:
                r = self.session.post(API, json=body, headers=headers, timeout=TIMEOUT)
            except requests.RequestException as e:
                last = f"{type(e).__name__} {e}"
                time.sleep(min(60, 2 ** attempt))
                continue
            self._count(seconds=time.time() - t0)
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code} {r.text[:200]}"
                wait = float(r.headers.get("Retry-After") or 0) or min(90, 5 * 2 ** attempt)
                print(f"  ! llm {last[:80]}; retry in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if not r.ok:
                self._count(errors=1)
                raise LlmError(f"HTTP {r.status_code}: {r.text[:400]}")
            d = r.json()
            if d.get("error"):  # OpenRouter reports upstream errors in a 200 body
                last = str(d["error"])[:300]
                time.sleep(min(60, 2 ** attempt))
                continue
            u = d.get("usage") or {}
            self._count(calls=1, prompt_tokens=u.get("prompt_tokens") or 0,
                        completion_tokens=u.get("completion_tokens") or 0)
            choice = (d.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            if not content.strip():
                last = f"empty reply (finish_reason {choice.get('finish_reason')})"
                continue
            return content
        self._count(errors=1)
        raise LlmError(f"gave up after {RETRIES + 1} attempts: {last}")
