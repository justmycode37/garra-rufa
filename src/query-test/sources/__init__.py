"""Registry of dataset sources. Each module defines one Source subclass.

When adding a source: register it in _MODULES and set `id_prefixes` / `by_name` on the
class (see the NOTE in base.Source). They decide which nodes the source is queried with.
"""
from .base import Edge, Node, Source

# module name -> Source subclass; import errors drop the source with a warning.
# OMIM was dropped: api.omim.org and its bulk files require a registered key.
# Orphanet gene associations live in orphadata (rd-associated-genes). FMA is read from
# EBI OLS4 (BioPortal would need a key). Helper modules (_ols) are not sources.
_MODULES = {
    "mondo": "MondoSource",
    "orphadata": "OrphadataSource",
    "disease_ontology": "DiseaseOntologySource",
    "nord": "NordSource",
    "monarch": "MonarchSource",
    "hpo": "HpoSource",
    "primekg": "PrimeKGSource",
    "uberon": "UberonSource",
    "fma": "FmaSource",
    "mesh": "MeshSource",
    "hpa": "HpaSource",
    "gtex": "GtexSource",
    "opentargets": "OpenTargetsSource",
    "clinvar": "ClinVarSource",
    "raresource": "RareSourceSource",
    # groups working on a disease: patient organisations, expert centres, networks,
    # research projects/consortia, trials and their sponsors (shared helpers in _groups).
    # Global Genes (globalgenes.org) is missing: it sits behind a Cloudflare challenge;
    # _brightdata can fetch it once BRIGHTDATA_API_KEY / BRIGHTDATA_ZONE are set.
    "orphanet_groups": "OrphanetGroupsSource",
    "ern": "ErnSource",
    "eurordis": "EurordisSource",
    "rdcrn": "RdcrnSource",
    "clinicaltrials": "ClinicalTrialsSource",
    "genetic_alliance_uk": "GeneticAllianceUkSource",
    "genetic_alliance_us": "GeneticAllianceUsSource",
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
