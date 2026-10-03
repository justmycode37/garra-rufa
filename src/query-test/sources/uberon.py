"""Uberon cross-species anatomy ontology via the EBI OLS4 API (no auth).

See _ols.py for the endpoints. For an UBERON:* node this yields subclass_of /
has_subclass, every OWL relation in both directions (part_of / has_part, located_in,
connected_to, develops_from, ... plus incoming ones such as GO processes that
"result in development of" it), and xrefs. FMA and MeSH xrefs become "xref" edges
(they are queried by the fma / mesh sources); all other xrefs (NCIT, SNOMED, UMLS,
mouse/zebrafish anatomy, ...) are kept on the node. Taxon constraints are skipped.
Free text: exact label/synonym match only.
"""
from ._ols import OlsOntologySource
from .base import Edge, Node

XREF_EDGE_PREFIXES = {"FMA": "FMA", "MESH": "MESH"}


class UberonSource(OlsOntologySource):
    name = "uberon"
    id_prefixes = frozenset({"UBERON"})
    ONTOLOGY = "uberon"
    PREFIX = "UBERON"

    def xref_edges(self, src: Node, xrefs: list[str]) -> list[Edge]:
        edges = []
        for x in xrefs:
            p, _, i = x.partition(":")
            if p.upper() in XREF_EDGE_PREFIXES:
                c = f"{XREF_EDGE_PREFIXES[p.upper()]}:{i}"
                edges.append(Edge(src, Node(c, c, "anatomy", self.name), "xref", self.name))
        return edges
