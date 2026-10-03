"""Orphanet (Orphadata Science) via its two public REST APIs (no registration).

  api.orphacode.org/EN/ClinicalEntity/...   nomenclature: name search, OMIM lookup,
      PreferentialParent / PreferentialChildren. It wants an `apiKey` header, but any
      non-empty value is accepted.
  api.orphadata.com/rd-phenotypes/...       HPO phenotypes per disorder / disorders per HPO term
  api.orphadata.com/rd-associated-genes/... genes per disorder / disorders per gene symbol
  api.orphadata.com/rd-classification/..    parents/children in every Orphanet classification
  api.orphadata.com/rd-cross-referencing/.. OMIM, MONDO, ICD-10/11, MeSH, UMLS, ... mappings

Node handling: free text -> name search ("matches"); ORPHA:* (or an ORPHA:* xref) ->
parents/children/phenotypes/genes/xrefs; OMIM:* -> Orphanet disorders mapped to it;
HP:* -> disorders showing that phenotype; gene nodes -> disorders for the gene symbol;
otherwise label search.
"""
from urllib.parse import quote

from .base import Edge, Node, Source

CODE_API = "https://api.orphacode.org/EN/ClinicalEntity"
DATA_API = "https://api.orphadata.com"
HEADERS = {"apiKey": "orphadata"}

FREQ_RANK = ["obligate", "very frequent", "frequent", "occasional", "very rare", "excluded"]
XREF_PREFIX = {"ICD-10": "ICD10", "ICD-11": "ICD11", "MeSH": "MESH", "MedDRA": "MEDDRA"}


def _freq_rank(freq: str | None) -> int:
    f = (freq or "").lower()
    for i, k in enumerate(FREQ_RANK):
        if f.startswith(k):
            return i
    return len(FREQ_RANK)


def _interleave(groups: list[list[Edge]], limit: int) -> list[Edge]:
    """Round-robin over relation groups so one big group cannot crowd out the rest."""
    out: list[Edge] = []
    i = 0
    while len(out) < limit and any(i < len(g) for g in groups):
        out += [g[i] for g in groups if i < len(g)][: limit - len(out)]
        i += 1
    return out


class OrphadataSource(Source):
    name = "orphadata"

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit)
        except Exception:
            return []

    # -- dispatch -----------------------------------------------------------
    def _query(self, node: Node, limit: int) -> list[Edge]:
        curies = ([node.id] if node.id else []) + list(node.xrefs)
        for c in curies:
            if c.split(":", 1)[0].upper() in ("ORPHA", "ORPHANET"):
                return self._disease(node, c.split(":", 1)[1], limit)
        for c in curies:
            p, _, i = c.partition(":")
            edges: list[Edge] = []
            if p.upper() in ("OMIM", "MIM"):
                edges = self._by_omim(node, i, limit)
            elif p.upper() == "HP":
                edges = self._by_hpo(node, c, limit)
            if edges:
                return edges
        if node.kind == "gene" or (node.id or "").startswith("HGNC:"):
            return self._by_gene(node, limit)
        if node.kind not in ("disease", "unknown", "term"):
            return []  # a label search for these would only produce spurious diseases
        return self._search(node, limit)

    # -- helpers ------------------------------------------------------------
    def _code_json(self, path: str):
        r = self.session.get(f"{CODE_API}/{path}", headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def _data_results(self, path: str):
        try:
            return self.get_json(f"{DATA_API}/{path}")["data"]["results"]
        except Exception:
            return None

    def _disease_node(self, code, label: str) -> Node:
        return Node(label, f"ORPHA:{code}", "disease", self.name)

    # -- free text ----------------------------------------------------------
    def _search(self, node: Node, limit: int) -> list[Edge]:
        label = node.label.strip()
        hits: list[tuple] = []
        exact = self._code_json(f"FindbyName/{quote(label, safe='')}")
        if isinstance(exact, dict) and "ORPHAcode" in exact:
            hits.append((exact["ORPHAcode"], exact["Preferred term"]))
        approx = self._code_json(f"ApproximateName/{quote(label, safe='')}")
        if isinstance(approx, list):
            low = label.lower()
            contained = [a for a in approx if low in a.get("Preferred term", "").lower()]
            hits += [(a["ORPHAcode"], a["Preferred term"]) for a in (contained or approx)]
        seen, edges = set(), []
        for code, term in hits:
            if code in seen:
                continue
            seen.add(code)
            edges.append(Edge(node, self._disease_node(code, term), "matches", self.name))
        return edges[:limit]

    # -- ORPHA disorder -----------------------------------------------------
    def _disease(self, node: Node, code: str, limit: int) -> list[Edge]:
        def mk(rel: str, dst: Node) -> Edge:
            return Edge(node, dst, rel, self.name)

        groups: list[list[Edge]] = []

        parent = self._code_json(f"orphacode/{code}/PreferentialParent")
        if isinstance(parent, dict) and isinstance(parent.get("Preferential parent"), dict):
            p = parent["Preferential parent"]
            groups.append([mk("subclass_of", self._disease_node(p["ORPHAcode"], p["Preferred term"]))])

        children = self._code_json(f"orphacode/{code}/PreferentialChildren")
        if isinstance(children, list):
            groups.append([mk("has_subclass", self._disease_node(c["ORPHAcode"], c["Preferred term"]))
                           for c in children])

        # every Orphanet classification has its own parents/children (no names in this product)
        cls = self._data_results(f"rd-classification/orphacodes/{code}/hchids")
        if isinstance(cls, list):
            pref = {e.dst.id for g in groups for e in g}
            for key, rel in (("parents", "subclass_of"), ("childs", "has_subclass")):
                ids = dict.fromkeys(c for h in cls for c in h.get(key) or [])
                groups.append([mk(rel, self._disease_node(c, f"ORPHA:{c}")) for c in ids
                               if f"ORPHA:{c}" not in pref])

        pheno = self._data_results(f"rd-phenotypes/orphacodes/{code}")
        if isinstance(pheno, dict):
            assoc = (pheno.get("Disorder") or {}).get("HPODisorderAssociation") or []
            assoc = [a for a in assoc  # "Excluded (0%)" means the phenotype is NOT present
                     if not (a.get("HPOFrequency") or "").lower().startswith("excluded")]
            assoc = sorted(assoc, key=lambda a: _freq_rank(a.get("HPOFrequency")))
            groups.append([mk("has_phenotype", Node(a["HPO"]["HPOTerm"], a["HPO"]["HPOId"],
                                                    "phenotype", self.name))
                           for a in assoc])

        genes = self._data_results(f"rd-associated-genes/orphacodes/{code}")
        if isinstance(genes, dict):
            ges = []
            for a in genes.get("DisorderGeneAssociation") or []:
                g = a["Gene"]
                refs = {x["Source"]: x["Reference"] for x in g.get("ExternalReference") or []}
                gid = f"HGNC:{refs['HGNC']}" if "HGNC" in refs else f"SYMBOL:{g['Symbol']}"
                xr = tuple(f"{s}:{v}" for s, v in refs.items() if s in ("OMIM", "Ensembl"))
                causal = "disease-causing" in (a.get("DisorderGeneAssociationType") or "").lower()
                ges.append(mk("caused_by_gene" if causal else "gene_associated",
                              Node(g["Symbol"], gid, "gene", self.name, xr)))
            ges.sort(key=lambda e: e.relation != "caused_by_gene")
            groups.append(ges)

        xrefs = self._data_results(f"rd-cross-referencing/orphacodes/{code}")
        if isinstance(xrefs, dict):
            xs = []
            for r in xrefs.get("ExternalReference") or []:
                src = XREF_PREFIX.get(r["Source"], r["Source"].upper())
                c = f"{src}:{r['Reference']}"
                xs.append(mk("xref", Node(c, c, "disease", self.name)))
            xs.sort(key=lambda e: not e.dst.id.startswith(("OMIM:", "MONDO:")))
            groups.append(xs)
        return _interleave(groups, limit)

    # -- other identifiers ---------------------------------------------------
    def _by_omim(self, node: Node, omim: str, limit: int) -> list[Edge]:
        d = self._code_json(f"FindbyOMIM/{omim}")
        if not isinstance(d, dict):
            return []
        return [Edge(node, self._disease_node(r["ORPHAcode"], r["Preferred term"]),
                     "xref", self.name) for r in d.get("References") or []][:limit]

    def _by_hpo(self, node: Node, hpo: str, limit: int) -> list[Edge]:
        res = self._data_results(f"rd-phenotypes/hpoids/{quote(hpo, safe=':')}")
        ranked = []
        for r in res if isinstance(res, list) else []:
            dis = r["Disorder"]
            freq = next((a.get("HPOFrequency") for a in dis.get("HPODisorderAssociation") or []
                         if a["HPO"]["HPOId"] == hpo), None)
            ranked.append((_freq_rank(freq), Edge(
                node, self._disease_node(dis["ORPHAcode"], dis["Preferred term"]),
                "phenotype_of", self.name)))
        ranked.sort(key=lambda t: t[0])
        return [e for _, e in ranked][:limit]

    def _by_gene(self, node: Node, limit: int) -> list[Edge]:
        # the API only matches lower-case symbols
        res = self._data_results(
            f"rd-associated-genes/genes/symbols/{quote(node.label.lower(), safe='')}")
        return [Edge(node, self._disease_node(r["ORPHAcode"], r["Preferred term"]),
                     "gene_associated", self.name)
                for r in (res if isinstance(res, list) else [])][:limit]
