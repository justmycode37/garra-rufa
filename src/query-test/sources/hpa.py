"""Human Protein Atlas (https://www.proteinatlas.org), public JSON, no auth.

  /{ENSG}.json                              full gene record
  /api/search_download.php?search=..&format=json&columns=g,eg
                                            gene search; also category queries such as
                                            "tissue_category_rna:liver;Tissue enriched"

Gene node (ENSEMBL id, or HGNC/SYMBOL/NCBIGene resolved by exact gene symbol) -> every
categorical relation in the record:
  expressed_in (tissue)            RNA tissue enriched/enhanced (nTPM, highest first)
  expressed_in_cell_type           RNA single-cell type, single-nuclei brain, blood cell,
                                   tissue cell-type enrichment
  expressed_in_brain_region        RNA brain regional specificity
  protein_expressed_in_*           protein tissue / cell type specificity
  expressed_in_cancer / _cell_line RNA cancer / cell line specificity
  prognostic_(un)favorable_in      cancer prognostics (TCGA / validation, prognostic only)
  located_in / secreted_to         subcellular (main + additional) / secretome location
  in_protein_class, has_function, involved_in, involved_in_disease   (UniProt keywords)
  in_expression_cluster            tissue / brain / cell line / single cell / blood cluster
Tissues are mapped to UBERON, cell types to CL, locations to GO through an exact OLS
match; anything unmapped gets an id like HPA:tissue/heart_muscle. Mapped nodes keep
their HPA id as xref, so a tissue / cell type / brain region node is looked up in
reverse: genes enriched (then enhanced) in it.
"""
from ._ols import exact_match, interleave
from .base import Edge, Node, Source, info

BASE = "https://www.proteinatlas.org"
SEARCH = f"{BASE}/api/search_download.php"

# record key -> (relation, HPA id category, OLS ontology to map to or None, node kind)
SPECIFIC = [
    ("RNA tissue specific nTPM", "expressed_in", "tissue", "uberon", "anatomy"),
    ("RNA single cell type specific nCPM", "expressed_in_cell_type", "celltype", "cl", "cell"),
    ("RNA single nuclei brain specific nCPM", "expressed_in_cell_type", "braincell", "cl", "cell"),
    ("RNA blood cell specific nTPM", "expressed_in_cell_type", "bloodcell", "cl", "cell"),
    ("RNA brain regional specific nTPM", "expressed_in_brain_region", "brainregion", "uberon",
     "anatomy"),
    ("Protein tissue specific Intensity", "protein_expressed_in", "tissue", "uberon", "anatomy"),
    ("Protein cell type specific Intensity", "protein_expressed_in_cell_type", "celltype", "cl",
     "cell"),
    ("RNA cancer specific pTPM", "expressed_in_cancer", "cancer", None, "disease"),
    ("RNA cell line specific nTPM", "expressed_in_cell_line", "cellline", None, "cell_line"),
]
LISTS = [
    ("Subcellular main location", "located_in", "location", "go", "component"),
    ("Subcellular additional location", "located_in", "location", "go", "component"),
    ("Secretome location", "secreted_to", "secretome", None, "component"),
    ("Protein class", "in_protein_class", "class", None, "protein_class"),
    ("Molecular function", "has_function", "keyword", None, "function"),
    ("Biological process", "involved_in", "keyword", None, "process"),
    ("Disease involvement", "involved_in_disease", "keyword", None, "keyword"),
    ("Tissue expression cluster", "in_expression_cluster", "cluster", None, "cluster"),
    ("Brain expression cluster", "in_expression_cluster", "cluster", None, "cluster"),
    ("Cell line expression cluster", "in_expression_cluster", "cluster", None, "cluster"),
    ("Single cell expression cluster", "in_expression_cluster", "cluster", None, "cluster"),
    ("Blood expression cluster", "in_expression_cluster", "cluster", None, "cluster"),
]
# HPA id category -> (search field, categories in order) for reverse lookups
REVERSE = {
    "tissue": ("tissue_category_rna", ["Tissue enriched,Group enriched", "Tissue enhanced"]),
    "celltype": ("cell_type_category_rna",
                 ["Cell type enriched,Group enriched", "Cell type enhanced"]),
    "brainregion": ("brain_category_rna", ["Region enriched,Group enriched", "Region enhanced"]),
}


def _hpa_id(category: str, name: str) -> str:
    return f"HPA:{category}/{name.strip().lower().replace(' ', '_')}"


class HpaSource(Source):
    name = "hpa"
    # HGNC/NCBIGene/SYMBOL genes are resolved by their exact symbol (label / SYMBOL: id)
    id_prefixes = frozenset({"ENSEMBL", "HGNC", "SYMBOL", "NCBIGENE", "HPA"})
    by_name = True

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit)
        except Exception:
            return []

    def _query(self, node: Node, limit: int) -> list[Edge]:
        if node.id is None:  # free text: only an exact gene symbol
            ens = self._by_symbol(node.label.strip())
            return [Edge(node, Node(node.label.strip().upper(), f"ENSEMBL:{ens}", "gene",
                                    self.name), "matches", self.name)] if ens else []
        for c in self.ids_for(node):
            p, _, local = c.partition(":")
            p = p.upper()
            if p == "ENSEMBL" and local.startswith("ENSG"):
                return self._gene(node, local.split(".")[0], limit)
            if p == "HPA":
                cat, _, name = local.partition("/")
                if cat in REVERSE:
                    return self._genes_in(node, cat, name.replace("_", " "), limit)
        if node.kind == "gene":
            for c in self.ids_for(node):
                p, _, local = c.partition(":")
                symbol = local if p.upper() == "SYMBOL" else node.label
                ens = self._by_symbol(symbol)
                if ens:
                    return self._gene(node, ens, limit)
        return []

    # -- helpers -----------------------------------------------------------
    def _search(self, search: str) -> list[dict]:
        r = self.session.get(SEARCH, timeout=60, params={
            "search": search, "format": "json", "columns": "g,eg", "compress": "no"})
        return r.json() if r.ok and r.text.lstrip().startswith("[") else []

    def _by_symbol(self, symbol: str) -> str | None:
        for row in self._search(symbol):
            if row.get("Gene", "").upper() == symbol.upper():
                return row["Ensembl"]
        return None

    def _named(self, name: str, category: str, ontology: str | None, kind: str) -> Node:
        hpa = _hpa_id(category, name)
        hit = exact_match(self.session, name, ontology) if ontology else None
        if not hit and ontology == "cl" and name.endswith("s"):  # "Fibroblasts" -> fibroblast
            hit = exact_match(self.session, name[:-1], ontology)
        if hit:
            return Node(hit[1], hit[0], kind, self.name, (hpa,))
        return Node(name, hpa, kind, self.name)

    # -- gene record -------------------------------------------------------
    def _gene(self, node: Node, ens: str, limit: int) -> list[Edge]:
        d = self.get_json(f"{BASE}/{ens}.json")
        xr = [f"ENSEMBL:{ens}", *(f"UniProtKB:{u}" for u in d.get("Uniprot") or [])]
        src = Node(node.label if node.id else d.get("Gene") or ens, node.id or f"ENSEMBL:{ens}",
                   "gene", node.source, tuple(dict.fromkeys((*node.xrefs, *xr))),
                   info(full_name=d.get("Gene description"),
                        synonyms=d.get("Gene synonym"),
                        url=f"https://www.proteinatlas.org/{ens}"))

        def mk(rel, dst):
            return Edge(src, dst, rel, self.name)

        groups: list[list[Edge]] = []
        for key, rel, cat, ont, kind in SPECIFIC:
            vals = d.get(key) or {}
            ranked = sorted(vals.items(), key=lambda kv: -float(kv[1] or 0))
            groups.append([mk(rel, self._named(n, cat, ont, kind)) for n, _ in ranked])
        enrich = d.get("RNA tissue cell type enrichment") or []
        groups.append([mk("expressed_in_cell_type",
                          self._named(e.split(" - ", 1)[-1], "celltype", "cl", "cell"))
                       for e in enrich])
        for key, rel, cat, ont, kind in LISTS:
            v = d.get(key)
            items = v if isinstance(v, list) else [v] if v else []
            groups.append([mk(rel, self._named(i, cat, ont, kind)) for i in items])
        prog = []
        for key, v in d.items():
            if key.startswith("Cancer prognostics - ") and isinstance(v, dict) \
                    and v.get("is_prognostic"):
                cancer = key[len("Cancer prognostics - "):].rsplit(" (", 1)[0]
                prog.append((float(v.get("p_val") or 1),
                             mk(f"prognostic_{v.get('prognostic type') or 'marker'}_in",
                                Node(cancer, _hpa_id("cancer", cancer), "disease", self.name))))
        groups.append([e for _, e in sorted(prog, key=lambda t: t[0])])
        return interleave(groups, limit)

    # -- reverse: genes enriched in a tissue / cell type / brain region ----
    def _genes_in(self, node: Node, category: str, name: str, limit: int) -> list[Edge]:
        field, tiers = REVERSE[category]
        edges: list[Edge] = []
        for tier, cats in enumerate(tiers):
            rel = "enriched_gene" if tier == 0 else "enhanced_gene"
            for row in self._search(f"{field}:{name};{cats}"):
                if len(edges) >= limit:
                    return edges
                edges.append(Edge(node, Node(row["Gene"], f"ENSEMBL:{row['Ensembl']}", "gene",
                                             self.name), rel, self.name))
        return edges
