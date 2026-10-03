"""Orphanet (Orphadata Science) via its two public REST APIs (no registration).

  api.orphacode.org/EN/ClinicalEntity/...   nomenclature: name search, OMIM lookup,
      PreferentialParent / PreferentialChildren. It wants an `apiKey` header, but any
      non-empty value is accepted.
  api.orphadata.com/rd-phenotypes/...       HPO phenotypes per disorder / disorders per HPO term
  api.orphadata.com/rd-associated-genes/... genes per disorder / disorders per gene symbol,
      typed by Orphanet's DisorderGeneAssociationType (see GENE_RELATION)
  api.orphadata.com/rd-classification/..    parents/children in every Orphanet classification
  api.orphadata.com/rd-cross-referencing/.. OMIM, MONDO, ICD-10/11, MeSH, UMLS, ... mappings

Node handling: free text -> name search ("matches"); ORPHA:* (or an ORPHA:* xref) ->
parents/children/phenotypes/genes/xrefs; OMIM:* -> Orphanet disorders mapped to it;
HP:* -> disorders showing that phenotype; HGNC:/SYMBOL:/ENSEMBL: genes -> disorders for
the gene symbol; anything else -> nothing. Gene nodes carry Orphanet's gene xrefs
(HGNC, Ensembl, OMIM, UniProt/SwissProt).

Extra data: an ORPHA disorder gets its Orphanet definition (orphacode/{code}/Definition)
and orpha.net page url (Node.info); disorder <-> gene edges carry the PMIDs of the
association's SourceOfValidation ("22587682[PMID]_...") as Edge.evidence.
"""
import re
from urllib.parse import quote

from .base import Edge, Node, Source, info

CODE_API = "https://api.orphacode.org/EN/ClinicalEntity"
DATA_API = "https://api.orphadata.com"
HEADERS = {"apiKey": "orphadata"}

FREQ_RANK = ["obligate", "very frequent", "frequent", "occasional", "very rare", "excluded"]
XREF_PREFIX = {"ICD-10": "ICD10", "ICD-11": "ICD11", "MeSH": "MESH", "MedDRA": "MEDDRA"}
# DisorderMappingRelation code -> edge relation; NTBT = the ORPHA term is narrower than the target
XREF_RELATION = {"E": "xref", "NTBT": "xref_broader", "BTNT": "xref_narrower"}
# DisorderGeneAssociationType prefix -> (relation from the disorder, relation from the gene),
# first match wins; disease-causing types sort first
GENE_RELATION = [
    ("disease-causing germline mutation(s) (loss of function)",
     ("caused_by_gene_loss_of_function", "causes_disease_loss_of_function")),
    ("disease-causing germline mutation(s) (gain of function)",
     ("caused_by_gene_gain_of_function", "causes_disease_gain_of_function")),
    ("disease-causing germline", ("caused_by_gene", "causes_disease")),
    ("disease-causing somatic", ("caused_by_somatic_mutation_in", "somatic_cause_of")),
    ("major susceptibility factor", ("susceptibility_gene", "susceptibility_factor_for")),
    ("modifying germline", ("modifier_gene", "modifies_disease")),
    ("part of a fusion gene", ("fusion_gene", "fusion_gene_in")),
    ("role in the phenotype", ("phenotype_role_gene", "role_in_phenotype_of")),
    ("candidate gene", ("candidate_gene", "candidate_gene_for")),
    ("biomarker", ("biomarker_gene", "biomarker_for")),
]
# Orphanet gene ExternalReference source -> CURIE prefix kept as gene xref
GENE_XREF = {"HGNC": "HGNC", "Ensembl": "ENSEMBL", "OMIM": "OMIM", "SwissProt": "UniProtKB"}
PAGE = "https://www.orpha.net/en/disease/detail/{}"
VALIDATION_PMID = re.compile(r"(\d+)\[PMID\]", re.I)


def _validation(a: dict) -> tuple[str, ...]:
    """PMIDs of a gene association's SourceOfValidation."""
    return tuple(dict.fromkeys(f"PMID:{int(x)}" for x in
                               VALIDATION_PMID.findall(a.get("SourceOfValidation") or "")))


def _freq_rank(freq: str | None) -> int:
    f = (freq or "").lower()
    for i, k in enumerate(FREQ_RANK):
        if f.startswith(k):
            return i
    return len(FREQ_RANK)


def _gene_relation(assoc_type: str | None) -> tuple[int, str, str]:
    """(sort rank, disorder->gene relation, gene->disorder relation)"""
    t = (assoc_type or "").lower()
    for i, (prefix, rels) in enumerate(GENE_RELATION):
        if t.startswith(prefix):
            return i, *rels
    return len(GENE_RELATION), "gene_associated", "gene_associated"


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
    # HGNC/ENSEMBL/SYMBOL genes are looked up by their symbol (the node label / SYMBOL: id)
    id_prefixes = frozenset({"ORPHA", "ORPHANET", "OMIM", "MIM", "HP", "HGNC", "SYMBOL",
                             "ENSEMBL"})
    by_name = True

    def __init__(self):
        super().__init__()
        self._names: dict[str, str | None] = {}  # ORPHAcode -> preferred term

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit)
        except Exception:
            return []

    # -- dispatch -----------------------------------------------------------
    def _query(self, node: Node, limit: int) -> list[Edge]:
        if node.id is None:
            return self._search(node, limit)
        curies = self.ids_for(node)
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
            elif p.upper() == "SYMBOL":
                edges = self._by_gene(node, i, limit)
            elif p.upper() in ("HGNC", "ENSEMBL") and node.kind == "gene":
                edges = self._by_gene(node, node.label, limit)
            if edges:
                return edges
        return []

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

    def _gene_node(self, g: dict) -> Node:
        refs = {x["Source"]: x["Reference"] for x in g.get("ExternalReference") or []}
        gid = f"HGNC:{refs['HGNC']}" if "HGNC" in refs else f"SYMBOL:{g['Symbol']}"
        xr = tuple(f"{GENE_XREF[s]}:{v}" for s, v in refs.items()
                   if s in GENE_XREF and s != "HGNC")
        return Node(g["Symbol"], gid, "gene", self.name, xr)

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
        d = self._code_json(f"orphacode/{code}/Definition")
        node = Node(node.label, node.id, node.kind, node.source, node.xrefs,
                    info(description=d.get("Definition") if isinstance(d, dict) else None,
                         url=PAGE.format(code)))

        def mk(rel: str, dst: Node, evidence=()) -> Edge:
            return Edge(node, dst, rel, self.name, evidence)

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
            ranked = []
            for a in genes.get("DisorderGeneAssociation") or []:
                rank, rel, _ = _gene_relation(a.get("DisorderGeneAssociationType"))
                assessed = (a.get("DisorderGeneAssociationStatus") or "").lower() == "assessed"
                ranked.append(((rank, not assessed),
                               mk(rel, self._gene_node(a["Gene"]), _validation(a))))
            groups.append([e for _, e in sorted(ranked, key=lambda t: t[0])])

        xrefs = self._data_results(f"rd-cross-referencing/orphacodes/{code}")
        if isinstance(xrefs, dict):
            xs = []
            for r in xrefs.get("ExternalReference") or []:
                src = XREF_PREFIX.get(r["Source"], r["Source"].upper())
                c = f"{src}:{r['Reference']}"
                # only exact mappings are "xref" (main.py merges those into one entity)
                mapping = (r.get("DisorderMappingRelation") or "-").split()[0]
                rel = XREF_RELATION.get(mapping, "xref_related")
                xs.append(mk(rel, Node(c, c, "disease", self.name)))
            xs.sort(key=lambda e: not e.dst.id.startswith(("OMIM:", "MONDO:")))
            groups.append(xs)
        return [self._named(e) for e in _interleave(groups, limit)]

    def _named(self, e: Edge) -> Edge:
        """Classification parents/children come without names (ORPHA:285014): look the
        name up, only for edges that made it into the result."""
        d = e.dst
        if d.id and d.id.startswith("ORPHA:") and d.label == d.id:
            code = d.id.split(":", 1)[1]
            if code not in self._names:
                r = self._code_json(f"orphacode/{code}/Name")
                self._names[code] = r.get("Preferred term") if isinstance(r, dict) else None
            if self._names[code]:
                return Edge(e.src, self._disease_node(code, self._names[code]), e.relation,
                            e.source)
        return e

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

    def _by_gene(self, node: Node, symbol: str, limit: int) -> list[Edge]:
        # the API only matches lower-case symbols
        res = self._data_results(
            f"rd-associated-genes/genes/symbols/{quote(symbol.lower(), safe='')}")
        src, ranked = node, []
        for r in res if isinstance(res, list) else []:
            for a in r.get("DisorderGeneAssociation") or []:
                if a["Gene"]["Symbol"].upper() != symbol.upper():
                    continue
                g = self._gene_node(a["Gene"])  # carries Orphanet's xrefs for this gene
                src = Node(node.label, node.id, "gene", node.source,
                           tuple(dict.fromkeys((*node.xrefs, *([g.id] if g.id != node.id
                                                               else []), *g.xrefs))))
                rank, _, rel = _gene_relation(a.get("DisorderGeneAssociationType"))
                assessed = (a.get("DisorderGeneAssociationStatus") or "").lower() == "assessed"
                ranked.append(((rank, not assessed), rel,
                               self._disease_node(r["ORPHAcode"], r["Preferred term"]),
                               _validation(a)))
        ranked.sort(key=lambda t: t[0])
        return [Edge(src, dst, rel, self.name, ev) for _, rel, dst, ev in ranked][:limit]
