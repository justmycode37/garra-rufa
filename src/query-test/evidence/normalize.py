"""LLM entity names -> ontology ids, so the same thing from different papers is one node.

In order, first hit wins:
  1. graph     the graph JSON's nodes (label, synonyms, aliases; context.Profile.index),
               when the node kind fits the entity type: reuses MONDO / HP / HGNC / ChEMBL /
               Reactome ids the graph already has
  2. ontology  OLS4 exact label/synonym match in the ontology for the type:
                 disease -> MONDO (then Orphanet/ORDO)   phenotype -> HP
                 anatomy -> UBERON                       cell_type -> CL
                 drug -> CHEBI                           process -> GO
               genes: HGNC REST by symbol / alias symbol
  3. pubtator  the paper's PubTator annotations with that name (MeSH / NCBI Gene ids)
  4. fuzzy     OLS non-exact search, top hit only when its words match the name
               (Jaccard >= FUZZY_MIN)
  5. text      "text:<type>:<slug>" (solutions such as model systems or outcome measures
               mostly live here; merged across papers by name)
Diseases resolved to MONDO also get their Orphanet / OMIM / MeSH xrefs (OLS term record).
"""
import re
import threading

from literature.base import Provider, Throttle
from sources.base import words

from .context import key

OLS = "https://www.ebi.ac.uk/ols4/api"
HGNC = "https://rest.genenames.org"
FUZZY_MIN = 0.6
ONTOLOGIES = {"disease": ["mondo", "ordo"], "phenotype": ["hp"], "anatomy": ["uberon"],
              "cell_type": ["cl"], "drug": ["chebi"], "process": ["go"],
              "pathway": ["go"], "biomarker": ["chebi"]}
# graph node kinds an entity type may resolve to
KINDS = {"disease": {"disease"}, "phenotype": {"phenotype", "disease"}, "gene": {"gene"},
         "drug": {"drug", "chemical"}, "therapy": {"drug"}, "biomarker": {"chemical", "gene"},
         "pathway": {"pathway", "process"}, "process": {"process", "pathway"},
         "anatomy": {"anatomy"}, "cell_type": {"cell"}}
PUBTATOR = {"disease": "Disease", "phenotype": "Disease", "gene": "Gene", "drug": "Chemical",
            "biomarker": "Chemical"}
KEEP_XREFS = ("Orphanet", "ORPHA", "OMIM", "MESH", "DOID", "GARD")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:80]


def jaccard(a: str, b: str) -> float:
    x, y = words(a), words(b)
    return len(x & y) / len(x | y) if x and y else 0.0


class Normalizer(Provider):
    name = "normalize"
    throttle = Throttle(0.1)

    def __init__(self, cache, profile):
        super().__init__(cache)
        self.profile = profile
        self._memo: dict[tuple, dict] = {}
        self._lock = threading.Lock()
        self.counts: dict[str, int] = {}

    def resolve(self, name: str, etype: str, synonyms=(), annotations=()) -> dict:
        """{id, label, how, xrefs}"""
        mk = (etype, key(name), tuple(sorted(key(s) for s in synonyms)))
        with self._lock:
            if mk in self._memo:
                return self._memo[mk]
        r = self._resolve(name, etype, list(synonyms), annotations)
        with self._lock:
            self._memo[mk] = r
            self.counts[r["how"]] = self.counts.get(r["how"], 0) + 1
        return r

    def _resolve(self, name, etype, synonyms, annotations) -> dict:
        names = [n for n in dict.fromkeys([name, *synonyms]) if n and len(key(n)) >= 2]
        kinds = KINDS.get(etype)
        # 1. graph
        if kinds:
            for n in names:
                for nid, label, kind in self.profile.index.get(key(n), []):
                    if kind in kinds:
                        return {"id": nid, "label": label, "how": "graph", "xrefs": []}
        # 2. ontology exact (also the singular: "fibroblasts" -> CL "fibroblast")
        singular = [n[:-1] for n in names if n.endswith("s") and not n.endswith("ss")
                    and len(n) > 4 and etype != "gene"]
        for n in dict.fromkeys(names + singular):
            hit = self._hgnc(n) if etype == "gene" else self._ols(n, etype, exact=True)
            if hit:
                return hit
        # 3. pubtator annotations of this paper
        want = PUBTATOR.get(etype)
        for a in annotations if want else []:
            if a.get("type") == want and key(a.get("name")) in {key(n) for n in names}:
                db = str(a.get("db_id") or "")
                if not db or db == "-":
                    continue
                cid = db if ":" in db else (f"NCBIGene:{db}" if want == "Gene" else f"MESH:{db}")
                return {"id": cid, "label": a["name"], "how": "pubtator", "xrefs": []}
        # 4. fuzzy ontology
        if etype != "gene":
            hit = self._ols(name, etype, exact=False)
            if hit:
                return hit
        return {"id": f"text:{etype}:{slug(name)}", "label": name, "how": "text", "xrefs": []}

    # -- services ------------------------------------------------------------------
    def _ols(self, name: str, etype: str, exact: bool) -> dict | None:
        for ont in ONTOLOGIES.get(etype, []):
            params = {"q": name, "ontology": ont, "type": "class", "rows": 3,
                      "queryFields": "label,synonym", "fieldList": "obo_id,label",
                      "obsoletes": "false", "exact": "true" if exact else "false"}
            try:
                docs = self.fetch_json(f"{OLS}/search", params)["response"]["docs"]
            except Exception as e:
                self.fail(f"ols {ont} {name}", e)
                continue
            for d in docs[:1 if exact else 3]:
                cid, label = d.get("obo_id"), d.get("label") or ""
                if not cid or ":" not in cid:
                    continue
                if not exact and jaccard(name, label) < FUZZY_MIN:
                    continue
                if ont == "ordo" and not cid.startswith("Orphanet"):
                    continue
                cid = cid.replace("Orphanet:", "ORPHA:")
                return {"id": cid, "label": label, "how": "ontology" if exact else "fuzzy",
                        "xrefs": self._xrefs(cid) if ont == "mondo" else []}
        return None

    def _xrefs(self, cid: str) -> list[str]:
        try:
            d = self.fetch_json(f"{OLS}/ontologies/mondo/terms", {"obo_id": cid})
            t = d["_embedded"]["terms"][0]
            xs = (t.get("annotation") or {}).get("database_cross_reference") or []
        except Exception as e:
            self.fail(f"xrefs {cid}", e)
            return []
        return [x.replace("Orphanet:", "ORPHA:") for x in xs if x.split(":")[0] in KEEP_XREFS]

    def _hgnc(self, name: str) -> dict | None:
        sym = name.strip().split()[0] if re.fullmatch(r"[A-Za-z0-9-]{2,15}( gene)?",
                                                        name.strip()) else None
        if not sym:
            return None
        for field in ("symbol", "alias_symbol", "prev_symbol"):
            try:
                d = self.fetch_json(f"{HGNC}/fetch/{field}/{sym.upper()}")
            except Exception as e:
                self.fail(f"hgnc {sym}", e)
                return None
            docs = (d.get("response") or {}).get("docs") or []
            if len(docs) == 1:
                return {"id": docs[0]["hgnc_id"], "label": docs[0]["symbol"],
                        "how": "ontology", "xrefs": []}
        return None

    def fetch_json(self, url, params=None, **kw):
        self.session.headers["Accept"] = "application/json"  # HGNC defaults to XML
        return super().fetch_json(url, params, **kw)
