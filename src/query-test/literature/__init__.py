"""Literature pipeline: collect research papers for the graph built by ../main.py.

Registry of literature providers, one module each (see base.Provider). "harvest" (the
papers the graph sources already cite, harvest.py) is not a provider but can be switched
off like one with --providers.
"""
from .base import Cache, Provider

# module name -> Provider subclass; order = order in which a query is sent to them
_MODULES = {
    "pubmed": "PubmedProvider",
    "europepmc": "EuropePmcProvider",
    "pubtator": "PubtatorProvider",
    "litvar": "LitVarProvider",
}


def load_providers(cache: Cache, only: list[str] | None = None) -> list[Provider]:
    import importlib
    import sys
    out = []
    for mod_name, cls_name in _MODULES.items():
        if only and mod_name not in only:
            continue
        try:
            mod = importlib.import_module(f".{mod_name}", __name__)
            out.append(getattr(mod, cls_name)(cache))
        except Exception as e:
            print(f"warning: provider {mod_name} unavailable: {e}", file=sys.stderr)
    return out


__all__ = ["Cache", "Provider", "load_providers"]
