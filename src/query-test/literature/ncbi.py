"""NCBI request spacing, shared by every provider on NCBI hosts.

E-utilities allow 3 requests/s per IP without an API key and 10/s with one (environment
NCBI_API_KEY, the same variable sources/clinvar.py uses). LitVar2 and PubTator 3 run on
www.ncbi.nlm.nih.gov and ask for at most 3 requests/s.
"""
import os

from .base import Throttle

API_KEY = os.environ.get("NCBI_API_KEY")
EUTILS = Throttle(0.11 if API_KEY else 0.35)
RESEARCH = Throttle(0.35)  # LitVar2 / PubTator 3


def eutils_params(**params) -> dict:
    return {"tool": "garra-rufa-literature", **params, **({"api_key": API_KEY} if API_KEY else {})}
