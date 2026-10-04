"""Background facts for the webapp's graph views (web_export.py), read from the local
indexes (garra.local, data/local/*.sqlite). Every lookup is optional: without an index
the field is left out and the view stays as present.py / evidence/main.py made it.

  phenotype (HP:)   HPO definition, layperson name (hp.obo "layperson" synonyms), whether
                    Orphanet lists it as a diagnostic criterion / pathognomonic sign of
                    the focus disease ("hallmark")
  gene              full name, cytogenetic locus (Orphanet), function (UniProt via Open
                    Targets), target class, tractability, tissue specificity (HPA),
                    subcellular location (HPA), Open Targets association with the disease
  drug              type, highest clinical stage, mechanisms of action, safety warnings
                    (Open Targets / ChEMBL)
  clinical trial    study type, phases, start / completion dates, acronym, posted results,
                    why it stopped (ClinicalTrials.gov)

enrich_present(view, graph) fills the overview items; enrich_evidence(view, kg) the
evidence nodes, plus what viewer_data() leaves out of the .kg.json: the year of each
quote, why each paper was screened in, per-paper extraction quality, the screening
funnel (papers judged relevant but not read) and the pipeline's own counts.
"""
import json
import re
import zlib

from sources import _local  # noqa: F401  (puts <repo>/src on sys.path: garra.local)

try:
    from garra.local import connect
except Exception:  # pragma: no cover - garra missing: no enrichment
    def connect(_name):
        return None

STAGE_LABEL = {"APPROVAL": "approved", "PHASE_4": "phase 4", "PHASE_3": "phase 3",
               "PHASE_2_3": "phase 2/3", "PHASE_2": "phase 2", "PHASE_1_2": "phase 1/2",
               "PHASE_1": "phase 1", "EARLY_PHASE_1": "early phase 1",
               "PRECLINICAL": "preclinical"}
UNREAD_LIMIT = 80  # screened-in papers that were not read, best first


def _clean(text: str | None) -> str | None:
    """UniProt function text without its evidence codes / PubMed citations."""
    if not text:
        return None
    text = re.sub(r"^\[[^\]]+\]:\s*", "", text)  # "[Fibrillin-1]: ..." isoform prefix
    text = re.sub(r"\s*\{ECO:[^}]*\}", "", text)
    text = re.sub(r"\s*\((?:PubMed:\d+(?:,\s*)?)+\)", "", text)
    return text.strip().rstrip(".") + "."


def _date(struct: dict | None) -> str | None:
    return (struct or {}).get("date")


class Local:
    """Lookups over the local indexes, cached per id."""

    def __init__(self):
        self.ont, self.ot = connect("ontology"), connect("opentargets")
        self.hpa, self.orpha = connect("hpa"), connect("orphadata")
        self.ct = connect("clinicaltrials")
        self._cache: dict[tuple, dict | None] = {}

    def _memo(self, key, fn):
        if key not in self._cache:
            try:
                self._cache[key] = fn() or None
            except Exception:  # a broken index row never breaks the export
                self._cache[key] = None
        return self._cache[key]

    # -- phenotypes ---------------------------------------------------------------------
    def phenotype(self, hp: str) -> dict | None:
        def get():
            if not self.ont:
                return None
            row = self.ont.execute("SELECT label, definition FROM term WHERE curie=?",
                                   (hp,)).fetchone()
            if not row:
                return None
            label, definition = row
            lay = [n for (n,) in self.ont.execute(
                "SELECT name FROM name WHERE curie=? AND type='layperson'", (hp,))]
            # the label itself may be the layperson term ("Tall stature")
            lay = [] if any(n.lower() == (label or "").lower() for n in lay) else lay
            return {k: v for k, v in (("definition", definition),
                                      ("lay", lay[0] if lay else None)) if v}
        return self._memo(("hp", hp), get)

    def hallmarks(self, orpha_codes) -> dict[str, str]:
        """HP id -> Orphanet's diagnostic criterion / pathognomonic sign, for the focus."""
        out: dict[str, str] = {}
        if not self.orpha:
            return out
        for code in orpha_codes:
            for hp, crit in self.orpha.execute(
                    "SELECT hp, criteria FROM pheno WHERE code=? AND criteria IS NOT NULL",
                    (code,)):
                if crit == "Pathognomonic sign" or hp not in out:
                    out[hp] = crit.lower()
        return out

    # -- genes --------------------------------------------------------------------------
    def hgnc(self, gid: str | None = None, symbol: str | None = None) -> tuple | None:
        if not self.ont:
            return None
        if gid and gid.upper().startswith("HGNC:"):
            row = self.ont.execute("SELECT symbol, name, ensembl FROM hgnc WHERE hgnc_id=?",
                                   (gid,)).fetchone()
            if row:
                return row
        if symbol and re.fullmatch(r"[A-Za-z0-9-]{2,15}", symbol):
            return self.ont.execute("SELECT symbol, name, ensembl FROM hgnc WHERE symbol=? "
                                    "AND status='Approved'", (symbol.upper(),)).fetchone()
        return None

    def gene(self, gid: str, label: str, disease: str | None = None) -> dict | None:
        def get():
            row = self.hgnc(gid, label)
            if not row:
                return None
            symbol, name, ensg = row
            out = {"symbol": symbol, "name": name}
            if self.orpha:
                for (assoc,) in self.orpha.execute("SELECT assoc FROM gene WHERE symbol=? "
                                                   "LIMIT 1", (symbol,)):
                    loci = (json.loads(assoc).get("Gene") or {}).get("Locus") or []
                    if loci:
                        out["locus"] = loci[0].get("GeneLocus")
            if self.ot and ensg:
                r = self.ot.execute("SELECT doc FROM target WHERE id=?", (ensg,)).fetchone()
                t = json.loads(r[0]) if r else {}
                fn = (t.get("functionDescriptions") or [None])[0]
                out["function"] = _clean(fn)
                classes = [c["label"] for c in t.get("targetClass") or ()]
                out["target_class"] = classes[-1] if classes else None
                out["tractable"] = sorted({x["label"] for x in t.get("tractability") or ()
                                           if x.get("value") and x["label"] in (
                                               "Approved Drug", "Advanced Clinical",
                                               "Phase 1 Clinical", "High-Quality Ligand",
                                               "High-Quality Pocket", "Druggable Family")})
                out["mouse"] = len(t.get("mousePhenotypes") or ())
            if self.hpa and ensg:
                r = self.hpa.execute("SELECT doc FROM gene WHERE ensg=?", (ensg,)).fetchone()
                h = json.loads(r[0]) if r else {}
                out["tissue_specificity"] = h.get("RNA tissue specificity")
                tissues = h.get("RNA tissue specific nTPM") or {}
                out["tissues"] = [t for t, _ in sorted(tissues.items(),
                                                       key=lambda kv: -float(kv[1]))][:4]
                out["cell_location"] = h.get("Subcellular main location") or []
            if self.ot and ensg and disease:
                ot_id = disease.replace(":", "_")
                r = self.ot.execute("SELECT score FROM assoc WHERE disease=? AND target=?",
                                    (ot_id, ensg)).fetchone()
                if r:
                    out["ot_score"] = round(r[0], 3)
            return {k: v for k, v in out.items() if v not in (None, [], "")}
        return self._memo(("gene", gid, label, disease), get)

    # -- drugs --------------------------------------------------------------------------
    def chembl(self, did: str, label: str) -> str | None:
        if did.upper().startswith("CHEMBL:"):
            return did.split(":", 1)[1].upper()
        if not self.ot:
            return None
        terms = [did.split(":", 1)[1]] if did.startswith("DrugBank:") else []
        terms.append(label.upper())
        for t in terms:
            r = self.ot.execute("SELECT id FROM alias WHERE term=? AND entity='drug'",
                                (t,)).fetchone()
            if r:
                return r[0]
        return None

    def drug(self, did: str, label: str) -> dict | None:
        def get():
            cid = self.chembl(did, label)
            if not cid or not self.ot:
                return None
            r = self.ot.execute("SELECT doc FROM drug WHERE id=?", (cid,)).fetchone()
            if not r:
                return None
            d = json.loads(r[0])
            mech = list(dict.fromkeys(m.get("mechanismOfAction") for m in d.get("mechanisms")
                                      or () if m.get("mechanismOfAction")))
            warn = list(dict.fromkeys(
                " · ".join(x for x in (w.get("warningType"), w.get("toxicityClass")) if x)
                for w in d.get("drugWarnings") or ()))
            out = {"chembl": cid, "type": d.get("drugType"),
                   "stage": STAGE_LABEL.get(d.get("maximumClinicalStage"),
                                            (d.get("maximumClinicalStage") or "").lower()),
                   "mechanisms": mech[:4], "warnings": [w for w in warn if w][:4]}
            return {k: v for k, v in out.items() if v not in (None, [], "")}
        return self._memo(("drug", did, label), get)

    # -- trials -------------------------------------------------------------------------
    def trial(self, nct: str) -> dict | None:
        def get():
            if not self.ct:
                return None
            r = self.ct.execute("SELECT doc FROM study WHERE nct=?",
                                (nct.split(":", 1)[-1],)).fetchone()
            if not r:
                return None
            raw = r[0]
            try:
                raw = zlib.decompress(raw)
            except Exception:
                pass
            d = json.loads(raw)
            p = d.get("protocolSection") or {}
            st, design = p.get("statusModule") or {}, p.get("designModule") or {}
            ident = p.get("identificationModule") or {}
            phases = [x.replace("PHASE", "phase ").replace("EARLY_", "early ").lower()
                      for x in design.get("phases") or () if x != "NA"]
            out = {"type": (design.get("studyType") or "").lower().replace("_", " "),
                   "phases": phases, "start": _date(st.get("startDateStruct")),
                   "end": _date(st.get("completionDateStruct"))
                   or _date(st.get("primaryCompletionDateStruct")),
                   "acronym": ident.get("acronym"), "results": bool(d.get("hasResults")),
                   "why_stopped": st.get("whyStopped"),
                   "official_title": ident.get("officialTitle")}
            return {k: v for k, v in out.items() if v not in (None, [], "", False)}
        return self._memo(("trial", nct), get)


def _orpha_codes(graph: dict | None, focus: dict) -> list[str]:
    codes = []
    for link in focus.get("links") or ():
        m = re.search(r"orpha\.net/en/disease/detail/(\d+)", link.get("url", ""))
        if m:
            codes.append(m[1])
    for n in (graph or {}).get("nodes") or ():
        if n["id"] == focus["id"]:
            codes += [x.split(":", 1)[1] for x in [n["id"], *n.get("xrefs", ())]
                      if x.upper().startswith("ORPHA:")]
    return list(dict.fromkeys(codes))


def _mondo(ids) -> str | None:
    return next((i for i in ids if i.upper().startswith("MONDO:")), None)


def enrich_present(view: dict, graph: dict | None = None) -> dict:
    loc = Local()
    focus = view["focus"]
    disease = _mondo([focus["id"]] + [x for n in (graph or {}).get("nodes") or ()
                                      if n["id"] == focus["id"] for x in n.get("xrefs", ())])
    marks = loc.hallmarks(_orpha_codes(graph, focus))
    for it in view["items"].values():
        iid, kind = it["id"], it["kind"]
        if iid.startswith("HP:"):
            info = loc.phenotype(iid) or {}
            if info.get("lay"):
                it["lay"] = info["lay"]
            if info.get("definition"):
                it["definition"] = info["definition"]
            if iid in marks:
                it["hallmark"] = marks[iid]
        elif kind == "gene":
            if g := loc.gene(iid, it["label"], disease):
                it["gene"] = g
        elif kind == "drug":
            if d := loc.drug(iid, it["label"]):
                it["drug"] = d
        elif kind == "clinical_trial":
            if t := loc.trial(iid):
                it["trial"] = t
    return view


def enrich_evidence(view: dict, kg: dict) -> dict:
    loc = Local()
    disease = _mondo([kg["profile"]["disease"]["id"], *kg["profile"]["disease"].get("xrefs", ())])
    for n in view["nodes"]:
        ids = [n["id"], *n.get("xrefs", ())]
        if n["kind"] == "gene":
            hg = next((i for i in ids if i.upper().startswith("HGNC:")), n["id"])
            if g := loc.gene(hg, n["label"], disease):
                n["gene"] = g
        elif n["kind"] == "drug":
            did = next((i for i in ids if i.upper().startswith(("CHEMBL:", "DRUGBANK:"))),
                       n["id"])
            if d := loc.drug(did, n["label"]):
                n["drug"] = d
        elif n["kind"] == "phenotype":
            hp = next((i for i in ids if i.startswith("HP:")), None)
            if hp and (p := loc.phenotype(hp)):
                n["phenotype"] = p

    # the year of each quote (viewer_data keeps paper / level / effect / ... only)
    for e_view, e_kg in zip(view["edges"], kg["edges"]):
        for q_view, q_kg in zip(e_view["evidence"], e_kg["evidence"]):
            if q_kg.get("year"):
                q_view["year"] = q_kg["year"]

    screen = {s["key"]: s for s in kg.get("screening") or () if "relevance" in s}
    read = {p["key"] for p in kg["papers"]}
    for p_view, p_kg in zip(view["papers"], kg["papers"]):
        s = screen.get(p_kg["key"]) or {}
        st = p_kg.get("stats") or {}
        p_view |= {k: v for k, v in {
            "relevance": s.get("relevance"), "connection": s.get("connection"),
            "why": s.get("why"), "solution_types": s.get("solution_types"),
            "truncated": p_kg.get("truncated"), "chars": p_kg.get("chars"),
            "quotes": st.get("quotes"), "unverified": st.get("unverified_quotes"),
        }.items() if v not in (None, [], "")}

    included = [s for s in screen.values() if s.get("include")]
    by_conn: dict[str, int] = {}
    for s in included:
        by_conn[s.get("connection") or "none"] = by_conn.get(s.get("connection") or "none", 0) + 1
    unread = sorted((s for s in included if s["key"] not in read),
                    key=lambda s: -(s.get("relevance") or 0))
    view["screening"] = {
        "screened": len(screen), "included": len(included), "read": len(read),
        "by_connection": dict(sorted(by_conn.items(), key=lambda kv: -kv[1])),
        "unread": [{k: s.get(k) for k in ("key", "title", "relevance", "connection", "why")}
                   for s in unread[:UNREAD_LIMIT]],
    }
    stats = kg.get("stats") or {}
    view["pipeline"] = {k: stats[k] for k in ("seconds", "text_sources", "extraction",
                                              "normalization") if k in stats}
    return view
