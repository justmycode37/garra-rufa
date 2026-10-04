"""Local copies of the upstream datasets (garra.local, see `python -m garra.local status`).
Not a source.

install(session)   route a requests.Session through the local indexes: requests they can
                   answer never reach the network, everything else does
serves(url)        whether a local index would try to answer `url` (callers skip their
                   politeness throttles then)
route(...)         the local answer (garra.local.router.Reply) or None
pubmed_has(ids)    the PMIDs held by the local PubMed subset

Without the garra package or the indexes every function is a no-op, so the sources keep
working against the live APIs. GARRA_LOCAL=0 disables local answers.
"""
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2])  # <repo>/src, home of the garra package
if _SRC not in sys.path:
    sys.path.append(_SRC)

try:
    from garra.local import pubmed as _pubmed
    from garra.local import router as _router
except Exception:  # pragma: no cover - garra missing or broken: live APIs only
    _router = _pubmed = None


def install(session) -> None:
    if _router:
        _router.install(session)


def serves(url: str) -> bool:
    return bool(_router) and _router.serves(url)


def route(method: str, url: str, params=None, data=None, json_body=None):
    return _router.route(method, url, params, data, json_body) if _router else None


def pubmed_has(pmids) -> set[str]:
    return _pubmed.has(pmids) if _pubmed and _router and _router.enabled() else set()
