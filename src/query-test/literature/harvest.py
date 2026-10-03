"""Papers the graph sources already cite (graph JSON of main.py, -o <file>.json).

  node info["refs"]   papers cited for an entity: definition citations (OLS), HPO term
                      references, ClinVar variant citations, trial references
                      (info["ref_types"]: BACKGROUND / RESULT / DERIVED)
  edge evidence       papers backing one association: HPO annotations (phenotype.hpoa),
                      Monarch association publications, Orphanet gene-disease validation,
                      Open Targets Europe PMC text-mining evidence

Every one becomes a Paper with a Hit of provider "graph:<source(s)>" and relation
"cited_for_entity" / "trial_reference:<type>" / "evidence:<edge relation>". They are
always kept (they do not count against --max-papers); pubmed/europepmc enrich() fills in
their metadata.
"""
from sources.base import paper_ref

from .base import Hit, Paper


def _paper(ref: str, hit: Hit) -> Paper | None:
    r = paper_ref(ref)
    if not r or r.startswith("URL:"):
        return None
    p = Paper(hits=[hit])
    if r.startswith("PMID:"):
        p.pmid = r[5:]
    elif r.startswith("PMC"):
        p.pmcid = r
    elif r.startswith("DOI:"):
        p.doi = r[4:]
    return p


def harvest(graph: dict) -> list[Paper]:
    out = []
    for n in graph["nodes"]:
        info = n.get("info") or {}
        types = info.get("ref_types") or {}
        for ref in info.get("refs") or []:
            rel = f"trial_reference:{types[ref].lower()}" if ref in types else "cited_for_entity"
            p = _paper(ref, Hit("graph:" + ",".join(n["sources"]), n["label"], n["id"], rel))
            if p:
                out.append(p)
    for e in graph["edges"]:
        for ref in e.get("evidence") or []:
            p = _paper(ref, Hit(f"graph:{e['source']}", f"{e['from']} --{e['relation']}--> "
                                f"{e['to']}", f"{e['from']}|{e['to']}",
                                f"evidence:{e['relation']}"))
            if p:
                out.append(p)
    return out
