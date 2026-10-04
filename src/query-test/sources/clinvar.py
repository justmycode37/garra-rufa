"""ClinVar via NCBI E-utilities (no key needed at <= 3 requests/s, 10/s with NCBI_API_KEY;
throttled here, HTTP 429 retried with backoff).

  esearch.fcgi?db=clinvar&term=..     variation ids for a gene ([GID], [HGNC], [gene]),
                                      a trait ([TRID]: MONDO_x, ORPHAx, HP_x, MedGen CUI,
                                      OMIM number) or a dbSNP id ([VRID])
  esummary.fcgi?db=clinvar&id=..      variant title, genes, molecular consequence,
                                      germline / somatic-oncogenicity / clinical-impact
                                      classification with the classified traits + xrefs

Gene / disease / phenotype node -> its variants (pathogenic and likely pathogenic
first), relation has_<classification>_variant (e.g. has_pathogenic_variant).
Variant node (ClinVar:<VariationID>, or dbSNP:rs..) -> in_gene, and one edge per
classified trait: <classification>_for (pathogenic_for, benign_for,
uncertain_significance_for, oncogenic_for, ...). Trait nodes are MONDO when ClinVar
knows the mapping, else ORPHA / OMIM / HP / MedGen, with the other ids as xrefs.
A disease lookup only keeps variants that really list the queried id on a trait (TRID
numbers are not prefix-specific, so "558" alone would also hit unrelated records).

Extra data (Node.info of variant nodes): the ClinVar page url and the papers ClinVar
links to the record (elink clinvar -> pubmed, one batched request per query; "refs").
"""
import os
import time

from . import _local
from ._ols import interleave, slug
from .base import TIMEOUT, Edge, Node, Source, info

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# NCBI allows 3 requests/s per IP without an API key, 10/s with one (env NCBI_API_KEY)
API_KEY = os.environ.get("NCBI_API_KEY")
MIN_INTERVAL = 0.11 if API_KEY else 0.35  # seconds between requests
RETRIES = 3  # on HTTP 429, with 1/2/4 s backoff
PATHOGENIC = '("clinsig pathogenic"[Properties] OR "clinsig likely pathogenic"[Properties])'
TRAIT_PREFIX = {"MONDO": "MONDO", "Orphanet": "ORPHA", "OMIM": "OMIM",
                "Human Phenotype Ontology": "HP", "MedGen": "UMLS"}
TRAIT_ORDER = ["MONDO", "ORPHA", "OMIM", "HP", "UMLS"]
# node prefix -> (trait id prefix used in _trait_ids, [TRID] search term format)
TRID = {"MONDO": ("MONDO", "MONDO_{}"), "ORPHA": ("ORPHA", "ORPHA{}"),
        "ORPHANET": ("ORPHA", "ORPHA{}"), "OMIM": ("OMIM", "{}"), "MIM": ("OMIM", "{}"),
        "HP": ("HP", "HP_{}"), "UMLS": ("UMLS", "{}"), "MEDGEN": ("UMLS", "{}")}
PAGE = "https://www.ncbi.nlm.nih.gov/clinvar/variation/{}/"
REFS_PER_VARIANT = 20
CLASSIFICATIONS = ("germline_classification", "oncogenicity_classification",
                   "clinical_impact_classification")


class ClinVarSource(Source):
    name = "clinvar"
    id_prefixes = frozenset({"CLINVAR", "DBSNP", "HGNC", "NCBIGENE", "SYMBOL", "MONDO",
                             "ORPHA", "ORPHANET", "OMIM", "MIM", "HP", "UMLS", "MEDGEN"})
    by_name = False
    focus_cap = 10

    def __init__(self):
        super().__init__()
        self._last = 0.0

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit)
        except Exception:
            return []

    def _get(self, util: str, **params):
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0 and not _local.serves(f"{EUTILS}/{util}.fcgi"):
            time.sleep(wait)
        params = {"db": "clinvar", "retmode": "json", "tool": "garra-rufa-query-test", **params,
                  **({"api_key": API_KEY} if API_KEY else {})}
        for attempt in range(RETRIES + 1):
            try:
                r = self.session.get(f"{EUTILS}/{util}.fcgi", params=params, timeout=TIMEOUT)
            finally:
                self._last = time.monotonic()
            # 429: NCBI's per-IP limit is shared with every other client on this address
            if r.status_code == 429 and attempt < RETRIES:
                time.sleep(float(r.headers.get("Retry-After") or 0) or 1.0 * 2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()

    def _search(self, term: str, n: int) -> list[str]:
        return self._get("esearch", term=term, retmax=n)["esearchresult"].get("idlist") or []

    def _summaries(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        res = self._get("esummary", id=",".join(ids))["result"]
        out = [res[i] for i in res.get("uids", []) if i in res]
        cites = self._citations([v["uid"] for v in out])
        for v in out:
            v["_refs"] = cites.get(v["uid"], [])
        return out

    def _citations(self, uids: list[str]) -> dict[str, list[str]]:
        """VariationID -> PMIDs ClinVar links to it (one elink, one linkset per id)."""
        if not uids:
            return {}
        try:
            d = self._get("elink", dbfrom="clinvar", db="pubmed", id=uids,
                          linkname="clinvar_pubmed")
        except Exception:
            return {}
        out = {}
        for ls in d.get("linksets") or []:
            pm = [f"PMID:{x}" for db in ls.get("linksetdbs") or [] for x in db.get("links") or []]
            for uid in ls.get("ids") or []:
                out[str(uid)] = pm[:REFS_PER_VARIANT]
        return out

    # -- dispatch ----------------------------------------------------------
    def _query(self, node: Node, limit: int) -> list[Edge]:
        for c in self.ids_for(node):
            p, _, local = c.partition(":")
            p = p.upper()
            if p == "CLINVAR":
                vid = local.removeprefix("VCV").lstrip("0")
                return self._variant(node, self._summaries([vid]), limit)
            if p == "DBSNP":
                ids = self._search(f"{local}[VRID]", limit)
                return self._variant(node, self._summaries(ids), limit)
        for c in self.ids_for(node):
            p, _, local = c.partition(":")
            p = p.upper()
            term = None
            if p == "NCBIGENE":
                term = f"{local}[GID]"
            elif p == "HGNC":
                term = f"{local}[HGNC]"
            elif p == "SYMBOL" and node.kind == "gene":
                term = f"{local}[gene]"
            if term:
                return self._listing(node, term, limit, None)
            if p in TRID:
                prefix, fmt = TRID[p]
                edges = self._listing(node, f"{fmt.format(local)}[TRID]", limit,
                                      f"{prefix}:{local}")
                if edges:
                    return edges
        return []

    # -- node builders -----------------------------------------------------
    def _variant_node(self, v: dict) -> Node:
        xr = []
        for vs in v.get("variation_set") or []:
            for x in vs.get("variation_xrefs") or []:
                if x["db_source"] == "dbSNP":
                    xr.append(f"dbSNP:rs{x['db_id']}")
                elif x["db_source"] == "ClinGen":
                    xr.append(f"ClinGen:{x['db_id']}")
        return Node(v.get("title") or v["uid"], f"ClinVar:{v['uid']}", "variant", self.name,
                    tuple(dict.fromkeys(xr)),
                    info(url=PAGE.format(v["uid"]), refs=v.get("_refs"),
                         genes=[g["symbol"] for g in v.get("genes") or [] if g.get("symbol")]))

    def _trait_ids(self, trait: dict) -> list[str]:
        ids = []
        for x in trait.get("trait_xrefs") or []:
            p = TRAIT_PREFIX.get(x["db_source"])
            if p:
                local = x["db_id"].split(":", 1)[-1]
                ids.append(f"{p}:{local}")
        return sorted(dict.fromkeys(ids), key=lambda c: TRAIT_ORDER.index(c.split(":")[0]))

    def _trait_node(self, trait: dict) -> Node | None:
        ids = self._trait_ids(trait)
        name = trait.get("trait_name") or ""
        if not ids or name.lower() in ("not provided", "not specified", "see cases"):
            return None
        kind = "phenotype" if ids[0].startswith("HP:") else "disease"
        return Node(name or ids[0], ids[0], kind, self.name, tuple(ids[1:]))

    @staticmethod
    def _sig(cls: dict) -> str:
        return slug((cls.get("description") or "").split(";")[0].split("/")[0]) or "unclassified"

    # -- gene / trait -> variants ------------------------------------------
    def _listing(self, node: Node, term: str, limit: int, trait_id: str | None) -> list[Edge]:
        n = max(limit * 3, 20) if trait_id else limit
        ids = self._search(f"{term} AND {PATHOGENIC}", n)
        if len(ids) < n:
            ids += [i for i in self._search(term, n) if i not in ids]
        edges = []
        for v in self._summaries(ids[:n]):
            if trait_id and not self._lists_trait(v, trait_id):
                continue
            sig = self._sig(v.get("germline_classification") or {})
            edges.append(Edge(node, self._variant_node(v), f"has_{sig}_variant", self.name))
        return edges[:limit]

    def _lists_trait(self, v: dict, trait_id: str) -> bool:
        return any(trait_id in self._trait_ids(t)
                   for c in CLASSIFICATIONS for t in (v.get(c) or {}).get("trait_set") or [])

    # -- variant -> genes / traits -----------------------------------------
    def _variant(self, node: Node, summaries: list[dict], limit: int) -> list[Edge]:
        groups: list[list[Edge]] = []
        for v in summaries:
            vn = self._variant_node(v)
            # a dbSNP node maps to its ClinVar records; a ClinVar node is the record itself
            src = node if node.id and node.id.startswith("ClinVar:") else vn
            if src is vn:
                groups.append([Edge(node, vn, "clinvar_record", self.name)])
            else:
                src = Node(node.label, node.id, "variant", node.source,
                           tuple(dict.fromkeys((*node.xrefs, *vn.xrefs))), vn.info)
            groups.append([Edge(src, Node(g["symbol"], f"NCBIGene:{g['geneid']}", "gene",
                                          self.name), "in_gene", self.name)
                           for g in v.get("genes") or [] if g.get("geneid")])
            for c in CLASSIFICATIONS:
                cls = v.get(c) or {}
                rel = f"{self._sig(cls)}_for"
                traits = [self._trait_node(t) for t in cls.get("trait_set") or []]
                groups.append([Edge(src, t, rel, self.name) for t in traits if t])
        return interleave(groups, limit)
