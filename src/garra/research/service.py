"""Bounded, read-only research requests, isolated from the HTTP server."""
import copy
import json
import os
import subprocess
import sys
import threading
import time
from collections import OrderedDict

from garra.ui.service import InputError


class ResearchUnavailable(Exception):
    pass


class ResearchService:
    def __init__(self):
        self._slots = threading.BoundedSemaphore(3)
        self._lock = threading.Lock()
        self._cache = OrderedDict()

    def search(self, body, *, papers=False):
        if not isinstance(body, dict) or set(body) - {"query", "limit", "category"}:
            raise InputError("Use query, limit, and optional category only")
        query = body.get("query")
        limit = body.get("limit", 12)
        category = body.get("category", "all")
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
            raise InputError("Search for a condition, gene, or research term (2–200 characters)")
        if any(ord(c) < 32 for c in query) or "://" in query:
            raise InputError("Use a research term, not a URL or multiline document")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise InputError("limit must be an integer from 1 to 20")
        if category not in ("all", "contacts"):
            raise InputError("category must be all or contacts")
        payload = {"query": query.strip(), "limit": limit, "category": category,
                   "mode": "papers" if papers else "graph"}
        key = json.dumps(payload, sort_keys=True)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                result = copy.deepcopy(cached[1])
                result["cached"] = True
                self._cache.move_to_end(key)
                return result
        if not self._slots.acquire(blocking=False):
            raise ResearchUnavailable("Research is busy. Please retry shortly.")
        try:
            process = subprocess.run(
                [sys.executable, "-m", "garra.research.worker"],
                input=json.dumps(payload), capture_output=True, text=True,
                timeout=65, env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            if process.returncode:
                raise ResearchUnavailable("The research pipeline could not complete. Please retry.")
            result = json.loads(process.stdout)
            if result["status"] == "unavailable":
                raise ResearchUnavailable("The research providers are unavailable. Please retry.")
            if result["status"] in ("ok", "empty"):
                with self._lock:
                    self._cache[key] = (time.monotonic() + 600, copy.deepcopy(result))
                    self._cache.move_to_end(key)
                    while len(self._cache) > 64:
                        self._cache.popitem(last=False)
            return result
        except subprocess.TimeoutExpired as exc:
            raise ResearchUnavailable("Research timed out. Try a more specific condition or gene.") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ResearchUnavailable("The research pipeline returned an incomplete response. Please retry.") from exc
        finally:
            self._slots.release()
