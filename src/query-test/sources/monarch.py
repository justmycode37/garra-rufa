"""Monarch Initiative knowledge graph (https://monarchinitiative.org).

Uses the public, no-auth Monarch API v3: https://api-v3.monarchinitiative.org/v3/api
  /search?q=...                  free-text / xref search (also resolves e.g. "OMIM:154700")
  /entity/{id}                   node details, xrefs and subclass hierarchy
  /association?subject=|object=  typed associations (disease-phenotype, gene-disease, ...)
Relations come from association categories listed in SPECS (disease-phenotype/gene/
variant/chemical/inheritance, gene-GO/interaction/homology/expression/phenotype, ...);
patient-level CaseTo* and GenotypeTo* associations are deliberately not used.
Monarch's canonical IDs are MONDO (disease), HP (phenotype) and HGNC (human gene);
other CURIEs (OMIM, ORPHA, ...) are resolved through xref search.
"""
from .base import Edge, Node, Source

API = "https://api-v3.monarchinitiative.org/v3/api"
DP = "biolink:DiseaseToPhenotypicFeatureAssociation"
CAUSAL = "biolink:CausalGeneToDiseaseAssociation"
CORREL = "biolink:CorrelatedGeneToDiseaseAssociation"
GP = "biolink:GeneToPhenotypicFeatureAssociation"
CHEM = "biolink:ChemicalEntityToDiseaseOrPhenotypicFeatureAssociation"
INHERIT = "biolink:DiseaseOrPhenotypicFeatureToGeneticInheritanceAssociation"
VARIANT = "biolink:VariantToDiseaseAssociation"
GO_BP = "biolink:MacromolecularMachineToBiologicalProcessAssociation"
GO_MF = "biolink:MacromolecularMachineToMolecularActivityAssociation"
GO_CC = "biolink:MacromolecularMachineToCellularComponentAssociation"
PPI = "biolink:PairwiseGeneToGeneInteraction"
HOMOLOG = "biolink:GeneToGeneHomologyAssociation"
EXPR = "biolink:GeneToExpressionSiteAssociation"

CANONICAL = {"MONDO": "disease", "HP": "phenotype", "HGNC": "gene"}
KINDS = {"biolink:Disease": "disease", "biolink:PhenotypicFeature": "phenotype",
         "biolink:Gene": "gene", "biolink:ChemicalEntity": "drug",
         "biolink:Drug": "drug", "biolink:Pathway": "pathway",
         "biolink:BiologicalProcess": "process", "biolink:MolecularActivity": "function",
         "biolink:CellularComponent": "component", "biolink:Cell": "anatomy",
         "biolink:AnatomicalEntity": "anatomy", "biolink:GrossAnatomicalStructure": "anatomy",
         "biolink:SequenceVariant": "variant"}
NORM = {"ORPHA": "Orphanet", "ORPHANET": "Orphanet"}

# kind -> [(query-param side, category, relation, direction)]; direction says whether
# the *other* end is the association's object ("obj") or subject ("subj").
SPECS = {
    "disease": [("subject", DP, "has_phenotype", "obj"),
                ("subject", INHERIT, "has_inheritance", "obj"),
                ("object", CAUSAL, "caused_by_gene", "subj"),
                ("object", CORREL, "gene_associated", "subj"),
                ("object", CHEM, "treated_by", "subj"),
                ("object", VARIANT, "has_variant", "subj")],
    "phenotype": [("object", DP, "phenotype_of", "subj"),
                  ("object", GP, "associated_gene", "subj")],
    "gene": [("subject", CAUSAL, "causes_disease", "obj"),
             ("subject", CORREL, "gene_associated_disease", "obj"),
             ("subject", GP, "has_phenotype", "obj"),
             ("subject", GO_BP, "involved_in", "obj"),
             ("subject", GO_MF, "has_function", "obj"),
             ("subject", GO_CC, "located_in", "obj"),
             ("subject", PPI, "protein_interacts_with", "obj"),
             ("subject", HOMOLOG, "ortholog_of", "obj"),
             ("subject", EXPR, "expressed_in", "obj")],
}


class MonarchSource(Source):
    name = "monarch"

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit)
        except Exception:
            return []

    def _query(self, node: Node, limit: int) -> list[Edge]:
        if node.kind in ("drug", "variant", "anatomy", "process", "function", "component",
                         "pathway", "exposure"):
            return []  # no Monarch relations for these; a label search would only add noise
        if node.id is None:
            return self._search(node, node.label, limit)
        for cand in (node.id, *node.xrefs):
            mid = self._resolve(cand)
            if mid:
                return self._relations(node, mid, limit)
        return self._search(node, node.label, limit)

    # -- helpers ---------------------------------------------------------
    def _resolve(self, curie: str) -> str | None:
        """Map a CURIE to a Monarch canonical ID (MONDO/HP/HGNC) or None."""
        if ":" not in curie:  # e.g. a free-text synonym carried in xrefs
            return None
        prefix = curie.split(":", 1)[0]
        if prefix in CANONICAL:
            return curie
        curie = NORM.get(prefix.upper(), prefix) + ":" + curie.split(":", 1)[1]
        data = self.get_json(f"{API}/search", params={"q": curie, "limit": 3})
        for it in data.get("items", []):
            if it["id"].split(":")[0] in CANONICAL and curie in (it.get("xref") or []):
                return it["id"]
        return None

    def _mknode(self, id_, label, category=None) -> Node:
        kind = KINDS.get(category) or CANONICAL.get(id_.split(":")[0], "term")
        return Node(label or id_, id=id_, kind=kind, source=self.name)

    def _search(self, node: Node, text: str, limit: int) -> list[Edge]:
        data = self.get_json(f"{API}/search", params={"q": text, "limit": limit})
        edges = []
        for it in data.get("items", [])[:limit]:
            dst = Node(it.get("name") or it["id"], id=it["id"],
                       kind=KINDS.get(it.get("category"), "term"), source=self.name,
                       xrefs=tuple(it.get("xref") or ()))
            edges.append(Edge(node, dst, "matches", self.name))
        return edges

    def _relations(self, node: Node, mid: str, limit: int) -> list[Edge]:
        kind = CANONICAL[mid.split(":")[0]]
        src = Node(node.label, id=mid, kind=kind, source=node.source, xrefs=node.xrefs)
        groups: list[list[Edge]] = []

        if kind in ("disease", "phenotype"):
            ent = self.get_json(f"{API}/entity/{mid}")
            hier = ent.get("node_hierarchy") or {}
            for rel, key in (("subclass_of", "super_classes"), ("has_subclass", "sub_classes")):
                groups.append([Edge(src, self._mknode(h["id"], h.get("name"), h.get("category")),
                                    rel, self.name) for h in (hier.get(key) or [])[:limit]])

        for side, cat, rel, direction in SPECS[kind]:
            try:
                data = self.get_json(f"{API}/association", params={
                    side: mid, "category": cat, "limit": limit * 3,
                    **({"direct": "true"} if side == "subject" and cat == DP else {})})
            except Exception:
                continue
            pre = "object" if direction == "obj" else "subject"
            groups.append([Edge(src, self._mknode(it[pre], it.get(pre + "_label"),
                                                  it.get(pre + "_category")), rel, self.name)
                           for it in data.get("items", [])])

        # round-robin over relation groups so one big group cannot crowd out the rest
        edges: list[Edge] = []
        seen: set[str] = {mid}
        i = 0
        while len(edges) < limit and any(i < len(g) for g in groups):
            for g in groups:
                if i < len(g) and len(edges) < limit and g[i].dst.id not in seen:
                    seen.add(g[i].dst.id)
                    edges.append(g[i])
            i += 1
        return edges
