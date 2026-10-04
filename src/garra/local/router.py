"""Answer upstream API requests from the local indexes.

Every handler emulates one API's response shape, so the code that parses API responses
stays as it is: a request goes to route(), and if a handler answers, the caller gets
that body instead of a network round trip. A handler returns None when it cannot answer
(index missing, endpoint or parameter it does not emulate) and the caller then makes the
real request. Set GARRA_LOCAL=0 to disable all local answers.

  route(method, url, params, data, json_body) -> Reply | None
  serves(url)                                 -> whether some handler would try this URL
  LocalAdapter                                   requests transport adapter using route()
"""

from __future__ import annotations

import importlib
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from garra.local import available

# handler modules, imported on first use; each calls register()
HANDLER_MODULES = ("ols", "hgnc", "jax", "mesh", "orphadata", "monarch", "opentargets",
                   "clinvar", "gtex", "hpa", "clinicaltrials", "reporter", "pubtator",
                   "litvar", "pubmed")


@dataclass
class Req:
    method: str
    url: str  # without query string
    host: str
    path: str
    params: dict[str, list[str]] = field(default_factory=dict)
    body: bytes | None = None

    def get(self, key: str, default: str | None = None) -> str | None:
        v = self.params.get(key)
        return v[0] if v else default

    def getall(self, key: str) -> list[str]:
        """All values; comma-separated values are split ("id=1,2" and "id=1&id=2")."""
        return [x for v in self.params.get(key) or [] for x in str(v).split(",") if x]

    def json(self):
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            return None


@dataclass
class Reply:
    body: object  # dict / list (sent as JSON), str or bytes
    status: int = 200
    content_type: str | None = None

    def bytes(self) -> bytes:
        if isinstance(self.body, bytes):
            return self.body
        if isinstance(self.body, str):
            return self.body.encode("utf-8")
        return json.dumps(self.body).encode("utf-8")

    def text(self) -> str:
        return self.body if isinstance(self.body, str) else self.bytes().decode("utf-8")

    def ctype(self) -> str:
        if self.content_type:
            return self.content_type
        return "application/json" if isinstance(self.body, (dict, list)) else "text/plain"


Handler = Callable[[Req], "Reply | None"]


@dataclass
class Route:
    host: str
    prefix: str
    needs: tuple[str, ...]  # local index names that must exist
    handler: Handler


_routes: list[Route] = []
_loaded = False
_lock = threading.Lock()


def register(host: str, prefix: str, needs: str | tuple[str, ...]):
    """Decorator: handle requests to https://<host><prefix>... when the indexes exist."""
    def deco(fn: Handler) -> Handler:
        _routes.append(Route(host, prefix, (needs,) if isinstance(needs, str) else needs, fn))
        return fn
    return deco


def enabled() -> bool:
    return os.environ.get("GARRA_LOCAL", "1").lower() not in ("0", "off", "false", "no")


def _load():
    global _loaded
    with _lock:
        if not _loaded:
            for m in HANDLER_MODULES:
                try:
                    importlib.import_module(f"garra.local.api_{m}")
                except ModuleNotFoundError as e:
                    if e.name != f"garra.local.api_{m}":
                        raise
            _loaded = True


def _match(host: str, path: str) -> list[Route]:
    _load()
    return [r for r in _routes if r.host == host and path.startswith(r.prefix)
            and all(available(n) for n in r.needs)]


def serves(url: str) -> bool:
    if not enabled():
        return False
    u = urlsplit(url)
    return bool(_match(u.hostname or "", u.path))


def _params(query: str, params, data) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {k: list(v) for k, v in parse_qs(query).items()}
    for src in (params, data):
        if isinstance(src, dict):
            for k, v in src.items():
                if v is None:
                    continue
                vals = v if isinstance(v, (list, tuple)) else [v]
                out.setdefault(k, []).extend(str(x) for x in vals)
        elif isinstance(src, (str, bytes)) and src:
            s = src.decode() if isinstance(src, bytes) else src
            if "=" in s and not s.lstrip().startswith(("{", "[")):
                for k, v in parse_qs(s).items():
                    out.setdefault(k, []).extend(v)
    return out


def route(method: str, url: str, params=None, data=None, json_body=None) -> Reply | None:
    if not enabled():
        return None
    u = urlsplit(url)
    routes = _match(u.hostname or "", u.path)
    if not routes:
        return None
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
    elif isinstance(data, (bytes, str)):
        body = data.encode() if isinstance(data, str) else data
    req = Req(method.upper(), f"{u.scheme}://{u.netloc}{u.path}", u.hostname or "", u.path,
              _params(u.query, params, data), body)
    for r in routes:
        t0 = time.perf_counter()
        try:
            reply = r.handler(req)
        except Exception as e:  # a broken local answer must never break the caller
            import sys
            print(f"[local] {r.handler.__module__}.{r.handler.__name__} failed on "
                  f"{url}: {e!r}", file=sys.stderr)
            reply = None
        if _PROFILE:
            _profile(r, req, reply, time.perf_counter() - t0)
        if reply is not None:
            return reply
    return None


# GARRA_LOCAL_PROFILE=<file>: append one TSV line per handler call (seconds, handler,
# answered, method, url, params, body) for finding slow queries (see `bench`)
_PROFILE = os.environ.get("GARRA_LOCAL_PROFILE")


def _profile(r: Route, req: Req, reply, dt: float) -> None:
    body = (req.body or b"").decode("utf-8", "replace")
    line = "\t".join((f"{dt:.4f}", r.handler.__module__.rsplit(".", 1)[-1],
                      "1" if reply is not None else "0", req.method, req.url,
                      json.dumps(req.params), json.dumps(body)))
    with _lock, open(_PROFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


try:
    import requests
    from requests.adapters import HTTPAdapter

    class LocalAdapter(HTTPAdapter):
        """Transport adapter: local answer when route() has one, else the network."""

        def send(self, request, **kwargs):
            reply = route(request.method or "GET", request.url, data=request.body)
            if reply is None:
                return super().send(request, **kwargs)
            r = requests.Response()
            r.status_code = reply.status
            r._content = reply.bytes()
            r.headers["Content-Type"] = reply.ctype()
            r.headers["X-Garra-Local"] = "1"
            r.encoding = "utf-8"
            r.url = request.url
            r.request = request
            r.reason = "OK" if reply.status < 400 else "Local error"
            return r

    def install(session) -> None:
        """Route a requests.Session through the local indexes."""
        adapter = LocalAdapter()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
except ImportError:  # requests is optional for the garra package
    def install(session) -> None:
        return None


def urllib_json(method: str, url: str, payload: dict | None = None) -> dict | list | None:
    """For urllib callers: the parsed local answer (status < 400) or None."""
    reply = route(method, url, json_body=payload)
    if reply is None or reply.status >= 400:
        return None
    return json.loads(reply.bytes())
