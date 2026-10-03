"""Registry of dataset sources. Each module defines one Source subclass."""
from .base import Edge, Node, Source

# module name -> Source subclass; import errors drop the source with a warning.
# OMIM was dropped: api.omim.org and its bulk files require a registered key.
_MODULES = {
    "mondo": "MondoSource",
    "orphadata": "OrphadataSource",
    "disease_ontology": "DiseaseOntologySource",
    "nord": "NordSource",
    "monarch": "MonarchSource",
    "hpo": "HpoSource",
    "primekg": "PrimeKGSource",
}


def load_sources() -> list[Source]:
    import importlib
    import sys
    sources = []
    for mod_name, cls_name in _MODULES.items():
        try:
            mod = importlib.import_module(f".{mod_name}", __name__)
            sources.append(getattr(mod, cls_name)())
        except Exception as e:
            print(f"warning: source {mod_name} unavailable: {e}", file=sys.stderr)
    return sources


__all__ = ["Edge", "Node", "Source", "load_sources"]
