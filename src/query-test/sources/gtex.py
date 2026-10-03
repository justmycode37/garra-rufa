"""GTEx Portal (https://gtexportal.org) API v2, no auth. Dataset gtex_v10 (GENCODE v39).

  /reference/gene?geneId=..                   symbol / Ensembl id -> versioned gencodeId
  /expression/medianGeneExpression            median TPM per tissue
  /association/singleTissueEqtl | Sqtl        significant eQTLs / sQTLs per gene
  /dataset/tissueSiteDetail                   the 54 tissues with their UBERON ids
  /expression/topExpressedGene                most expressed genes of a tissue
  /association/egene                          eGenes of a tissue (q-value ordered)
  /dataset/variant?snpId=..                    dbSNP rs id -> GTEx variant ids

Gene node (ENSEMBL id, or HGNC/SYMBOL/NCBIGene by exact symbol) -> expressed_in
tissues (median TPM >= 1, highest first; tissues are UBERON nodes), has_eqtl and
has_sqtl variants (dbSNP rs ids, strongest p-value over all tissues first; which
tissue a QTL was found in is not kept). UBERON node that is a GTEx tissue ->
top_expressed_gene and has_egene. dbSNP / GTEx variant -> eqtl_of / sqtl_of genes.
"""
from ._ols import interleave
from .base import Edge, Node, Source

API = "https://gtexportal.org/api/v2"
DATASET = "gtex_v10"
GENCODE = "v39"
MIN_TPM = 1.0


class GtexSource(Source):
    name = "gtex"
    # HGNC/NCBIGene/SYMBOL genes are resolved by their exact symbol (label / SYMBOL: id)
    id_prefixes = frozenset({"ENSEMBL", "HGNC", "SYMBOL", "NCBIGENE", "UBERON", "DBSNP",
                             "GTEX"})
    by_name = False

    def __init__(self):
        super().__init__()
        self._tissues: dict[str, list[dict]] | None = None  # UBERON id -> tissue records

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit)
        except Exception:
            return []

    def _query(self, node: Node, limit: int) -> list[Edge]:
        for c in self.ids_for(node):
            p, _, local = c.partition(":")
            p = p.upper()
            if p == "ENSEMBL" and local.startswith("ENSG"):
                gene = self._gene_record(local.split(".")[0])
                return self._gene(node, gene, limit) if gene else []
            if p == "UBERON":
                tissues = self._tissue_map().get(f"UBERON:{local}")
                if tissues:
                    return self._tissue(node, tissues, limit)
            if p == "GTEX" and local.endswith("_b38"):
                return self._variant(node, [local], limit)
            if p == "DBSNP" and local.lower().startswith("rs"):
                # rs id -> GRCh38 variant ids (the same in v8 and v10)
                vids = {v["variantId"] for v in self._data("dataset/variant", snpId=local.lower())}
                return self._variant(node, sorted(vids), limit) if vids else []
        if node.kind == "gene":
            for c in self.ids_for(node):
                p, _, local = c.partition(":")
                if p.upper() in ("HGNC", "SYMBOL", "NCBIGENE"):
                    gene = self._gene_record(local if p.upper() == "SYMBOL" else node.label)
                    return self._gene(node, gene, limit) if gene else []
        return []

    # -- helpers -----------------------------------------------------------
    def _data(self, path: str, **params) -> list[dict]:
        return self.get_json(f"{API}/{path}", params=params).get("data") or []

    def _gene_record(self, gene_id: str) -> dict | None:
        for g in self._data("reference/gene", geneId=gene_id, gencodeVersion=GENCODE,
                            genomeBuild="GRCh38/hg38"):
            if gene_id.upper() in (g["geneSymbolUpper"], g["gencodeId"].split(".")[0]):
                return g
        return None

    def _tissue_map(self) -> dict[str, list[dict]]:
        if self._tissues is None:
            self._tissues = {}
            for t in self._data("dataset/tissueSiteDetail", datasetId=DATASET):
                self._tissues.setdefault(t["ontologyId"], []).append(t)
        return self._tissues

    def _gene_node(self, d: dict) -> Node:
        return Node(d["geneSymbol"], f"ENSEMBL:{d['gencodeId'].split('.')[0]}", "gene",
                    self.name)

    def _tissue_node(self, d: dict) -> Node:
        t = next((t for ts in self._tissue_map().values() for t in ts
                  if t["tissueSiteDetailId"] == d["tissueSiteDetailId"]), None)
        label = t["tissueSiteDetail"] if t else d["tissueSiteDetailId"].replace("_", " ")
        return Node(label, d["ontologyId"], "anatomy", self.name)

    def _variant_node(self, d: dict) -> Node:
        if (d.get("snpId") or "").startswith("rs"):
            return Node(d["snpId"], f"dbSNP:{d['snpId']}", "variant", self.name,
                        (f"GTEx:{d['variantId']}",))
        return Node(d["variantId"], f"GTEx:{d['variantId']}", "variant", self.name)

    @staticmethod
    def _best(rows: list[dict], key: str) -> list[dict]:
        """Strongest association per `key` (a variant / gene can appear in many tissues)."""
        best: dict[str, dict] = {}
        for r in rows:
            k = r.get(key)
            if k and (k not in best or r["pValue"] < best[k]["pValue"]):
                best[k] = r
        return sorted(best.values(), key=lambda r: r["pValue"])

    # -- gene ----------------------------------------------------------------
    def _gene(self, node: Node, gene: dict, limit: int) -> list[Edge]:
        gid = gene["gencodeId"]
        ens = f"ENSEMBL:{gid.split('.')[0]}"
        src = Node(node.label, node.id, "gene", node.source,
                   tuple(dict.fromkeys((*node.xrefs, ens, f"NCBIGene:{gene['entrezGeneId']}"))))
        expr = self._data("expression/medianGeneExpression", gencodeId=gid, datasetId=DATASET)
        expr = sorted((e for e in expr if e["median"] >= MIN_TPM), key=lambda e: -e["median"])
        groups = [[Edge(src, self._tissue_node(e), "expressed_in", self.name) for e in expr]]
        for path, rel in (("association/singleTissueEqtl", "has_eqtl"),
                          ("association/singleTissueSqtl", "has_sqtl")):
            rows = self._best(self._data(path, gencodeId=gid, datasetId=DATASET), "variantId")
            groups.append([Edge(src, self._variant_node(r), rel, self.name) for r in rows])
        return interleave(groups, limit)

    # -- tissue --------------------------------------------------------------
    def _tissue(self, node: Node, tissues: list[dict], limit: int) -> list[Edge]:
        groups: list[list[Edge]] = []
        for t in tissues:
            top = self._data("expression/topExpressedGene", tissueSiteDetailId=t[
                "tissueSiteDetailId"], datasetId=DATASET, filterMtGene="true")
            groups.append([Edge(node, self._gene_node(g), "top_expressed_gene", self.name)
                           for g in top])
            eg = self._data("association/egene", tissueSiteDetailId=t["tissueSiteDetailId"],
                            datasetId=DATASET, itemsPerPage=max(limit, 10))
            eg.sort(key=lambda g: g.get("qValue", 1))
            groups.append([Edge(node, self._gene_node(g), "has_egene", self.name) for g in eg])
        return interleave(groups, limit)

    # -- variant -------------------------------------------------------------
    def _variant(self, node: Node, variant_ids: list[str], limit: int) -> list[Edge]:
        groups = []
        for path, rel in (("association/singleTissueEqtl", "eqtl_of"),
                          ("association/singleTissueSqtl", "sqtl_of")):
            rows = self._best(self._data(path, variantId=variant_ids, datasetId=DATASET),
                              "gencodeId")
            groups.append([Edge(node, self._gene_node(r), rel, self.name) for r in rows])
        return interleave(groups, limit)
