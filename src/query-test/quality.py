"""Relevance / noise rules applied by main.run() and used by the report.

  similar()      does a name-search hit share a meaningful word with the query?
  is_noise()     non-human diseases, obsolete terms, taxa, lab codes, model-organism
                 phenotypes
  is_generic()   inheritance modes and other nodes that link to almost everything
  specificity()  0..1, how informative a node is as a "connection" (hub weighting)
"""
import math
import sqlite3
from functools import lru_cache
from pathlib import Path

from sources import Node, _hpoa
from sources.base import similar  # noqa: F401  (re-exported for main.py)

# taxa, lab test codes, animal disease registry, model-organism phenotype ontologies
# (model-organism genes, i.e. orthologs, are kept: they are real research connections)
NOISE_PREFIXES = {"NCBITAXON", "LOINC", "OMIA", "MP", "ZP", "WBPHENOTYPE", "FBCV", "XPO",
                  "DDPHENO", "APO"}
GENERIC_PREFIXES = {"GENO", "OTAR"}
PRIMEKG_DB = Path(__file__).resolve().parents[2] / "data" / "primekg" / "primekg.sqlite"


def _prefix(curie: str | None) -> str:
    return (curie or "").split(":", 1)[0].upper()


def is_noise(node: Node) -> bool:
    if _prefix(node.id) in NOISE_PREFIXES:
        return True
    if any(_prefix(x) == "NCBITAXON" for x in node.xrefs):
        return True
    # MONDO keeps human diseases in the 0xxxxxx range; the others are animal diseases
    if _prefix(node.id) == "MONDO" and not node.id.split(":", 1)[1].startswith("0"):
        return True
    label = node.label.lower()
    return "non-human animal" in label or label.startswith("obsolete ")


def is_generic(node: Node) -> bool:
    """Nodes not worth expanding or counting as a connection (inheritance modes, ...)."""
    if _prefix(node.id) in GENERIC_PREFIXES:
        return True
    hpo = _hpoa.load()
    ids = [i for i in (node.id, *node.xrefs) if _prefix(i) == "HP"]
    return bool(hpo and ids and any(hpo.is_inheritance(i) for i in ids))


@lru_cache(maxsize=None)
def _primekg_degree(curies: tuple[str, ...], label: str, kind: str) -> int | None:
    """Edges to diseases/phenotypes of the node in PrimeKG (protein interactions and GO
    terms excluded, so a gene's degree reflects its disease links)."""
    if not PRIMEKG_DB.exists():
        return None
    db = sqlite3.connect(f"file:{PRIMEKG_DB}?mode=ro", uri=True)
    try:
        row = None
        for c in curies:
            row = db.execute("SELECT idx FROM nodes WHERE curie=?", (c,)).fetchone()
            if row:
                break
        if not row and kind == "gene":
            row = db.execute("SELECT idx FROM nodes WHERE name_lc=? AND type='gene'",
                             (label.lower(),)).fetchone()
        if not row:
            return None
        return db.execute("SELECT count(*) FROM edges e JOIN nodes n ON n.idx=e.y WHERE "
                          "e.x=? AND n.type IN ('disease','phenotype')", row).fetchone()[0]
    finally:
        db.close()


def specificity(node_id: str, ids: set[str], label: str, kind: str) -> float:
    """0..1: how much a link through this node says (1 = very specific). Phenotypes use
    HPO information content, everything else its PrimeKG disease/phenotype degree."""
    if _prefix(node_id) in GENERIC_PREFIXES:
        return 0.0
    hpo = _hpoa.load()
    hps = [i for i in (node_id, *ids) if _prefix(i) == "HP"]
    if hpo and hps:
        return min(hpo.specificity(h) for h in hps)
    deg = _primekg_degree(tuple(sorted({node_id, *ids})), label, kind)
    if deg is None:
        return 0.5
    # degree 1 -> ~1, 30 -> ~0.6, 300 -> ~0.35, 3000 -> ~0.1
    return max(0.05, 1 - math.log10(1 + deg) / 4)
