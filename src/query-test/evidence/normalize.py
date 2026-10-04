"""LLM entity names -> ontology ids, so the same thing from different papers is one node.

Each entity is looked up by its expanded name first (extract.py: "alanine aminotransferase"
for "ALT"), then its name and synonyms. Candidates are collected in order:
  1. graph     the graph JSON's nodes (label, synonyms, aliases; context.Profile.index),
               when the node kind fits the entity type: reuses MONDO / HP / HGNC / ChEMBL /
               Reactome ids the graph already has
  2. ontology  OLS4 exact label/synonym match in the ontology for the type:
                 disease -> MONDO (then Orphanet/ORDO)   phenotype -> HP
                 anatomy -> UBERON                       cell_type -> CL
                 drug -> CHEBI                           process -> GO
               genes: HGNC REST by symbol / alias symbol
  3. pubtator  the paper's PubTator annotations with that name (MeSH / NCBI Gene ids)
  4. fuzzy     OLS non-exact search, top hit whose words match the name
               (Jaccard >= FUZZY_MIN), only when nothing above matched; the hit
               must still carry the name as its label or a synonym, uniquely
  5. text      "text:<type>:<slug of the expanded name>" (solutions such as model systems
               or outcome measures mostly live here; merged across papers by name)

Short abbreviations (is_short: up to 3 letters/digits with a capital, "ALT", "MAD", "Hp")
are never looked up in OLS (ChEBI, MONDO, ...) or the graph index unless the paper defines
the entity's own name as that abbreviation: as ontology synonyms they mostly name
something else (ALT -> altrose, AST -> astressin, MAD -> Met-Ala-Asp, Hp -> heptyl group).
HGNC symbol lookups are exempt (gene symbols are short by design).

A candidate whose label is the entity's own (expanded) name is taken as is. Otherwise one
LLM call per paper (confirm(): all of the paper's open entities in one batch, cached)
picks the candidate that denotes the same thing, or none, which makes the entity a text
node. Without an LLM the first candidate wins (the old behaviour, still guarded).
Ambiguous names and fuzzy matches never establish identity on their own.
Diseases resolved to MONDO also get their Orphanet / OMIM / MeSH xrefs (OLS term record).
"""
import json
import re
import threading

from literature.base import Provider, Throttle
from sources.base import words

from .context import key

OLS = "https://www.ebi.ac.uk/ols4/api"
HGNC = "https://rest.genenames.org"
FUZZY_MIN = 0.6
MAX_CANDIDATES = 4
SHORT = re.compile(r"(?=.*[A-Z])[A-Za-z0-9]{1,3}")
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

CONFIRM_SYSTEM = """You check entity -> ontology mappings for a biomedical knowledge graph.
For each entity (as named in a paper, with its expanded form and type) you get candidate
ontology terms. Pick the candidate that denotes the SAME thing: the same molecule, protein,
gene, disease, phenotype, process, cell type or tissue. Accept exact synonyms, spelling or
plural variants, a gene and its protein product, the formal ontology name of the same
structure or cell ("eyes" ~ "camera-type eye", "aortic root" ~ "bulb of aorta"), and a
phenotype term that is a clinical synonym ("epilepsy" ~ "Seizure"). Reject a different thing that merely shares an
abbreviation or a word (the enzyme ALT is not the sugar altrose; mitral annulus
disjunction is not the peptide Met-Ala-Asp), a protein or lectin mapped to a small
molecule, and a clearly broader or narrower concept ("protein glycosylation" is not
"sialylation"). When in doubt, choose none.
Answer with one JSON object: {"answers": [{"i": <entity index>, "choice": <candidate index
or null>}]}, one entry per entity."""


def is_short(name: str) -> bool:
    """A short abbreviation-like token ("ALT", "MAD", "Hp", "CRP")."""
    return bool(SHORT.fullmatch((name or "").strip()))


def _singular(k: str) -> str:
    return " ".join(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w
                    for w in k.split())


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:80]


def jaccard(a: str, b: str) -> float:
    x, y = words(a), words(b)
    return len(x & y) / len(x | y) if x and y else 0.0


class Normalizer(Provider):
    name = "normalize"
    throttle = Throttle(0.1)

    def __init__(self, cache, profile, llm=None):
        super().__init__(cache)
        self.profile = profile
        self.llm = llm
        self._memo: dict[tuple, list[dict]] = {}
        self._choice: dict[tuple, str | None] = {}
        self._lock = threading.Lock()
        self.counts: dict[str, int] = {}

    # -- public --------------------------------------------------------------------
    def resolve(self, name: str, etype: str, synonyms=(), annotations=(), expanded: str = "",
                defined: bool = False) -> dict:
        """{id, label, how, xrefs} of one entity (confirmed by the LLM when one is set)."""
        ent = {"name": name, "type": etype, "synonyms": list(synonyms),
               "expanded": expanded or name, "defined": defined}
        return self.resolve_many({"e": ent}, annotations)["e"]

    def resolve_many(self, ents: dict[str, dict], annotations=(), title: str = "") -> dict:
        """key -> {id, label, how, xrefs} for extract.py entities {name, type, expanded,
        defined, synonyms}; one confirmation call for all of them."""
        cands = {k: self.candidates(e, annotations) for k, e in ents.items()}
        out, ask = {}, {}
        for k, e in ents.items():
            cs = cands[k]
            mk = self._choice_key(e, cs)
            with self._lock:
                known = mk in self._choice
                chosen = self._choice.get(mk)
            if not cs:
                out[k] = self._text(e)
            elif known:
                out[k] = next((c for c in cs if c["id"] == chosen), None) or self._text(e)
            elif self._trivial(e, cs[0]) or self.llm is None:
                out[k] = cs[0]
            else:
                ask[k] = e
        if ask:
            picks = self.confirm(ask, cands, title)
            for k, e in ask.items():
                c = picks.get(k)
                with self._lock:
                    self._choice[self._choice_key(e, cands[k])] = c["id"] if c else None
                out[k] = c or {**self._text(e), "rejected": [x["id"] for x in cands[k]]}
        with self._lock:
            for r in out.values():
                self.counts[r["how"]] = self.counts.get(r["how"], 0) + 1
                if r.get("rejected"):
                    self.counts["llm_rejected"] = self.counts.get("llm_rejected", 0) + 1
        return out

    # -- candidates ----------------------------------------------------------------
    def candidates(self, e: dict, annotations=()) -> list[dict]:
        etype, name, expanded = e["type"], e["name"], e.get("expanded") or e["name"]
        mk = (etype, key(name), key(expanded), bool(e.get("defined")),
              tuple(sorted(key(s) for s in e.get("synonyms") or [])))
        with self._lock:
            if mk in self._memo:
                return self._memo[mk]
        r = self._candidates(e, etype, name, expanded, annotations)
        with self._lock:
            self._memo[mk] = r
        return r

    @staticmethod
    def _names(e, etype, name, expanded) -> list[str]:
        """Names worth a lookup: the expanded form first (genes: the symbol first),
        without short abbreviations unless the paper defines the entity's own name as one
        (genes keep their symbols)."""
        first = [name, expanded] if etype == "gene" else [expanded, name]
        names = [n for n in dict.fromkeys([*first, *(e.get("synonyms") or [])])
                 if n and len(key(n)) >= 2]
        if etype == "gene":
            return names
        return [n for n in names if not is_short(n) or (n == name and e.get("defined"))]

    def _candidates(self, e, etype, name, expanded, annotations) -> list[dict]:
        out: list[dict] = []

        def add(c) -> bool:
            if c and all(c["id"] != x["id"] for x in out):
                out.append(c)
            return len(out) >= MAX_CANDIDATES

        names = self._names(e, etype, name, expanded)
        kinds = KINDS.get(etype)
        # 1. graph
        if kinds:
            for n in names:
                for nid, label, kind in self.profile.index.get(key(n), []):
                    if kind in kinds and add({"id": nid, "label": label, "how": "graph",
                                              "xrefs": []}):
                        return out
        # 2. ontology exact (also the singular: "fibroblasts" -> CL "fibroblast")
        singular = [n[:-1] for n in names if n.endswith("s") and not n.endswith("ss")
                    and len(n) > 4 and etype != "gene"]
        found = 0
        for n in dict.fromkeys(names + singular):
            hit = self._hgnc(n) if etype == "gene" else self._ols(n, etype, exact=True)
            if hit:
                found += 1
                if add(hit) or found >= 2:
                    break
        # 3. pubtator annotations of this paper
        want = PUBTATOR.get(etype)
        keys = {key(n) for n in names}
        for a in annotations if want else []:
            if a.get("type") == want and key(a.get("name")) in keys:
                db = str(a.get("db_id") or "")
                if not db or db == "-":
                    continue
                cid = db if ":" in db else (f"NCBIGene:{db}" if want == "Gene" else f"MESH:{db}")
                add({"id": cid, "label": a["name"], "how": "pubtator", "xrefs": []})
                break
        # 4. fuzzy ontology, only when nothing matched exactly
        if not out and etype != "gene" and names:
            add(self._ols(names[0], etype, exact=False))
        return out

    @staticmethod
    def _text(e: dict) -> dict:
        label = e.get("expanded") or e["name"]
        return {"id": f"text:{e['type']}:{slug(label)}", "label": label, "how": "text",
                "xrefs": []}

    @staticmethod
    def _choice_key(e, cs) -> tuple:
        return (e["type"], key(e["name"]), key(e.get("expanded") or ""),
                tuple(c["id"] for c in cs))

    @staticmethod
    def _trivial(e: dict, c: dict) -> bool:
        """The candidate's label is the entity's own (expanded) name or gene symbol."""
        lab = _singular(key(c["label"]))
        return any(n and lab == _singular(key(n))
                   and (not is_short(n) or e["type"] == "gene" or e.get("defined"))
                   for n in (e["name"], e.get("expanded") or ""))

    # -- LLM confirmation ------------------------------------------------------------
    def confirm(self, ents: dict[str, dict], cands: dict[str, list[dict]],
                title: str = "") -> dict[str, dict | None]:
        """key -> the candidate the LLM accepts (None: no candidate is the same thing)."""
        keys = list(ents)
        rows = []
        for i, k in enumerate(keys):
            e = ents[k]
            rows.append({"i": i, "type": e["type"], "name": e["name"],
                         "expanded": e.get("expanded") or e["name"],
                         "synonyms": (e.get("synonyms") or [])[:5],
                         "candidates": [{"j": j, "id": c["id"], "label": c["label"]}
                                        for j, c in enumerate(cands[k])]})
        user = (f"Paper: {title[:300]}\n\nEntities (choose a candidate j or null):\n"
                + "\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        try:
            out = self.llm.chat(CONFIRM_SYSTEM, user, max_tokens=8000)
        except Exception as e:  # keep the paper; take only the unambiguous names
            self.fail(f"confirm {len(keys)} entities", e)
            return {k: cands[k][0] for k in keys if not is_short(ents[k]["name"])}
        res: dict[str, dict | None] = {}
        for a in (out or {}).get("answers") or []:
            if not isinstance(a, dict):
                continue
            try:
                k = keys[int(a.get("i"))]
            except (TypeError, ValueError, IndexError):
                continue
            ch = a.get("choice")
            try:
                res[k] = cands[k][int(ch)] if ch is not None else None
            except (TypeError, ValueError, IndexError):
                res[k] = None
        return res

    # -- services ------------------------------------------------------------------
    def _ols(self, name: str, etype: str, exact: bool) -> dict | None:
        for ont in ONTOLOGIES.get(etype, []):
            params = {"q": name, "ontology": ont, "type": "class", "rows": 3,
                      "queryFields": "label,synonym", "fieldList": "obo_id,label,synonym",
                      "obsoletes": "false", "exact": "true" if exact else "false"}
            try:
                docs = self.fetch_json(f"{OLS}/search", params)["response"]["docs"]
            except Exception as e:
                self.fail(f"ols {ont} {name}", e)
                continue
            verified = [d for d in docs if key(name) in
                        {key(x) for x in [d.get("label", ""), *(d.get("synonym") or [])]}]
            if len({d.get("obo_id") for d in verified if d.get("obo_id")}) != 1:
                continue
            for d in verified[:1]:
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
