"""Call OpenAI to narrate a pre-built evidence packet."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_MODEL = "gpt-4o-mini"
MAX_OUTPUT_CHARS = 12_000

SYSTEM_PROMPT = """You help rare-disease patient group leaders understand evidence-backed connections.
You receive a fixed list of citations (E1, E2, ...). You MUST NOT invent facts, people, trials, or URLs.
Every factual sentence in sections must include at least one citation id from the list.
If coverage gaps exist, say what is unknown and what to verify next.
Return JSON only, matching the schema described in the user message."""


def _schema_hint() -> str:
    return json.dumps(
        {
            "title": "short headline",
            "summary_for_family": "2-4 sentences, plain language, cite ids inline like [E1]",
            "sections": [
                {
                    "heading": "Why this connection matters",
                    "body": "paragraph with [E#] citations",
                    "citations": ["E1"],
                }
            ],
            "uncertainties": ["bullet strings about gaps or warnings"],
            "next_step_this_week": "one concrete action",
        },
        indent=2,
    )


def call_openai(packet: dict, *, model: str | None = None, api_key: str | None = None) -> dict:
    key = (api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    model = (model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL).strip()
    user_content = (
        "Evidence packet (only facts you may use):\n"
        + json.dumps(packet, ensure_ascii=False, indent=2)
        + "\n\nOutput JSON schema:\n"
        + _schema_hint()
    )

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    request = Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=90) as response:
            raw = response.read(MAX_OUTPUT_CHARS * 4)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"OpenAI request failed: {exc}") from exc

    payload = json.loads(raw)
    content = payload["choices"][0]["message"]["content"]
    narrative = json.loads(content)
    narrative["mode"] = "openai"
    narrative["model"] = model
    return narrative
