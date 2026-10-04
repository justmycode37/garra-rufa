"""Human Disease Ontology (DOID) via the EBI OLS4 REST API (no auth).

Endpoints (https://www.ebi.ac.uk/ols4/api):
  /search?q=<label>&ontology=doid        free-text search -> "matches"
  /ontologies/doid/terms/<double-encoded IRI>   term record (xrefs, synonyms)
  .../parents, .../children               class hierarchy
  .../disease_has_location                anatomical location links (UBERON)

CURIE nodes: DOID:* is looked up directly; other nodes are resolved through any
DOID:* entry in node.xrefs, otherwise by label search. OLS has no reverse xref
lookup, so e.g. an OMIM-only node falls back to its label.
"""
from urllib.parse import quote

from ._ols import SEARCH_FIELDS, search_info, v1_info
from .base import Edge, Node, Source

OLS = "https://www.ebi.ac.uk/ols4/api"
ONTOLOGY = "doid"
PREFIX = "DOID"
OBO = "http://purl.obolibrary.org/obo/"

# normalise xref prefixes to the CURIE prefixes used across the project
PREFIX_MAP = {"Orphanet": "ORPHA", "ORPHANET": "ORPHA", "SCTID": "SNOMEDCT",
              "MIM": "OMIM", "NCI": "NCIT", "UMLS_CUI": "UMLS", "icd11.foundation": "ICD11"}
STRUCTURAL_LINKS = {"self", "parents", "ancestors", "hierarchicalParents", "hierarchicalAncestors",
                    "jstree", "children", "descendants", "hierarchicalChildren",
                    "hierarchicalDescendants", "graph"}
LINK_RELATIONS = {  # OLS relation link name -> (edge relation, node kind)
    "disease_has_location": ("has_location", "anatomy"),
}


def _kind(curie: str) -> str:
    p = curie.split(":", 1)[0]
    return {"HGNC": "gene", "HP": "phenotype", "UBERON": "anatomy"}.get(p, "disease")


def _target_kind(curie: str) -> str:
    return {"HGNC": "gene", "HP": "phenotype", "UBERON": "anatomy", "MONDO": "disease",
            "DOID": "disease"}.get(curie.split(":", 1)[0], "term")


class DiseaseOntologySource(Source):
    name = "disease_ontology"
    id_prefixes = frozenset({PREFIX})
    by_name = True

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        if node.kind not in ("disease", "unknown", "term"):
            return []  # label-searching phenotypes/genes/drugs would only match unrelated diseases
        try:
            return self._query(node, limit)
        except Exception:
            return []

    def _query(self, node: Node, limit: int) -> list[Edge]:
        if node.id is None:
            return self._search(node, limit)
        ids = self.ids_for(node)
        if not ids:
            return []
        return self._relations(node, PREFIX + ":" + ids[0].split(":", 1)[1], limit)

    def _search(self, node: Node, limit: int) -> list[Edge]:
        d = self.get_json(f"{OLS}/search", params={
            "q": node.label, "ontology": ONTOLOGY, "rows": limit,
            "obsoletes": "false", "type": "class",
            "fieldList": SEARCH_FIELDS})
        edges = []
        for doc in d["response"]["docs"]:
            if not doc.get("obo_id", "").startswith(PREFIX + ":"):
                continue
            dst = Node(doc["label"], doc["obo_id"], "disease", self.name,
                       info=search_info(doc))
            edges.append(Edge(node, dst, "matches", self.name))
        return edges[:limit]

    def _term_url(self, curie: str) -> str:
        iri = OBO + curie.replace(":", "_")
        return f"{OLS}/ontologies/{ONTOLOGY}/terms/{quote(quote(iri, safe=''), safe='')}"

    def _embedded(self, url: str, size: int) -> list[dict]:
        try:
            return self.get_json(url, params={"size": size}).get("_embedded", {}).get("terms", [])
        except Exception:
            return []

    def _relations(self, node: Node, curie: str, limit: int) -> list[Edge]:
        url = self._term_url(curie)
        term = self.get_json(url)
        node = Node(node.label, node.id, node.kind, node.source, node.xrefs,
                    v1_info(term, ONTOLOGY))
        groups: list[list[Edge]] = []

        def add(rel: str, dst: Node):
            return Edge(node, dst, rel, self.name)

        for rel, link in (("subclass_of", "parents"), ("has_subclass", "children")):
            es = []
            for t in self._embedded(f"{url}/{link}", limit):
                if t.get("obo_id", "").startswith(PREFIX + ":"):
                    es.append(add(rel, Node(t["label"], t["obo_id"], "disease", self.name)))
            groups.append(es)

        for link in term.get("_links", {}):
            if link in STRUCTURAL_LINKS:
                continue
            rel, kind = LINK_RELATIONS.get(link, (link, None))
            es = []
            for t in self._embedded(f"{url}/{link}", limit):
                cur = _iri_to_curie(t.get("iri", ""))
                if cur:
                    es.append(add(rel, Node(t.get("label") or cur, cur, kind or _target_kind(cur),
                                            self.name)))
            groups.append(es)

        xr = []
        for x in term.get("annotation", {}).get("database_cross_reference", []):
            p, _, i = x.partition(":")
            p = "SNOMEDCT" if p.startswith("SNOMEDCT") else PREFIX_MAP.get(p, p)
            c = f"{p}:{i}"
            if c.startswith(("ORPHA:", "OMIM:", "MONDO:")):
                xr.insert(0, c)  # prefer the disease databases we query
            else:
                xr.append(c)
        groups.append([add("xref", Node(c, c, _kind(c), self.name)) for c in xr])
        return _interleave(groups, limit)


def _iri_to_curie(iri: str) -> str | None:
    if iri.startswith(OBO):
        return iri[len(OBO):].replace("_", ":", 1)
    return None


def _interleave(groups: list[list[Edge]], limit: int) -> list[Edge]:
    """Round-robin over relation groups so one big group cannot crowd out the rest."""
    out: list[Edge] = []
    i = 0
    while len(out) < limit and any(i < len(g) for g in groups):
        out += [g[i] for g in groups if i < len(g)][: limit - len(out)]
        i += 1
    return out
