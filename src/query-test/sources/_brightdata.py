"""Fetch pages that block plain HTTP clients (Cloudflare challenges) through the Bright
Data Web Unlocker API. Not a source.

Configuration (environment, see .env.example):
  BRIGHTDATA_API_KEY   API key from the Bright Data control panel
  BRIGHTDATA_ZONE      name of a Web Unlocker zone (e.g. "web_unlocker1")

  POST https://api.brightdata.com/request
       Authorization: Bearer <key>
       {"zone": <zone>, "url": <target>, "format": "raw"}   -> the target's body

Without the variables, available() is False and sources that need it return nothing.
Each request is billed by Bright Data, so callers should cache what they fetch.
"""
import os

from .base import Source

API = "https://api.brightdata.com/request"


def available() -> bool:
    return bool(os.environ.get("BRIGHTDATA_API_KEY") and os.environ.get("BRIGHTDATA_ZONE"))


def fetch(src: Source, url: str, timeout: int = 120) -> str:
    """Body of `url` fetched through the Web Unlocker (raises on failure)."""
    if not available():
        raise RuntimeError("BRIGHTDATA_API_KEY / BRIGHTDATA_ZONE not set")
    r = src.session.post(API, timeout=timeout,
                         headers={"Authorization": f"Bearer {os.environ['BRIGHTDATA_API_KEY']}"},
                         json={"zone": os.environ["BRIGHTDATA_ZONE"], "url": url,
                               "format": "raw"})
    r.raise_for_status()
    return r.text
