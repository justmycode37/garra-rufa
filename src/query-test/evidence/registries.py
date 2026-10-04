"""Trial registries and drug databases: interventions already tested in the input disease,
drugs that act on a mechanism, and whether anyone has tried a drug in the disease yet.

  ClinicalTrials.gov  /api/v2/studies  trials of the disease (MeSH id, else its names) with
                      their interventions and phase; trials of drug X in the disease
                      (query.cond + query.intr). Always the live API: the local index
                      (garra.local) keeps no interventions.
  Open Targets        GraphQL: the disease's drug and clinical candidates (with stage), a
                      target's drugs (mechanisms of action from ChEMBL), drug search by name
  ChEMBL              /molecule/search + /mechanism: mechanisms of action of compounds Open
                      Targets does not list (preclinical tools, research compounds)
  Europe PMC          hit counts of "<drug> AND <disease>" (title/abstract), used by the
                      gap pass to tell "nobody has tried this" from "barely tried"

query_hygiene (../improvements.py): disease and drug names are sanitised before they go
into a query (a synonym "Alstrom\\" made ClinicalTrials.gov answer HTTP 400).

DrugBank needs a licence and is not used; ChEMBL / Open Targets cover drug-target
mechanisms. Everything goes through the literature cache (data/literature-cache/), errors
are logged, never raised. Requests the local indexes answer (Open Targets, Europe PMC)
never reach the network.

registry_records() turns the disease's trials and Open Targets candidates into records of
the same shape as an extracted paper (main.read_paper), so build.Graph adds them like
papers: "<intervention> tested_in <disease>", evidence level "registered_trial", the
trial's title as the quote and the NCT id as the passage.
"""
import json
import re
from types import SimpleNamespace

import improvements
from literature.base import Cache, Provider, Throttle
from sources import _local

from .context import key, sanitize_terms
from .normalize import is_short

CTGOV = "https://clinicaltrials.gov/api/v2/studies"
OT = "https://api.platform.opentargets.org/api/v4/graphql"
CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CT_FIELDS = ",".join([
    "protocolSection.identificationModule.nctId",
    "protocolSection.identificationModule.briefTitle",
    "protocolSection.statusModule.overallStatus",
    "protocolSection.statusModule.startDateStruct",
    "protocolSection.designModule.phases",
    "protocolSection.designModule.studyType",
    "protocolSection.conditionsModule",
    "protocolSection.armsInterventionsModule.interventions",
    "protocolSection.descriptionModule.briefSummary",
])
# intervention types that are solutions (not "OTHER" placebo / questionnaires)
CT_TYPES = {"DRUG": "drug", "BIOLOGICAL": "therapy", "GENETIC": "therapy",
            "DIETARY_SUPPLEMENT": "drug", "PROCEDURE": "therapy", "DEVICE": "therapy",
            "COMBINATION_PRODUCT": "drug", "RADIATION": "therapy"}
NOT_SOLUTION = re.compile(r"^(placebo|standard of care|usual care|control|no intervention|"
                          r"observation|questionnaire|blood (draw|sample)|sham)\b", re.I)
OT_DISEASE_Q = """query($id:String!){ disease(efoId:$id){ id name
 drugAndClinicalCandidates{rows{maxClinicalStage drug{id name}}} } }"""
OT_TARGET_Q = """query($id:String!){ target(ensemblId:$id){ id approvedSymbol
 drugAndClinicalCandidates{rows{maxClinicalStage drug{id name}}} } }"""
OT_DRUG_Q = """query($id:String!){ drug(chemblId:$id){ id name
 mechanismsOfAction{rows{actionType mechanismOfAction targets{id approvedSymbol}}}
 indications{rows{maxClinicalStage disease{id name}}} } }"""
OT_SEARCH_Q = """query($q:String!,$e:[String!]!){ search(queryString:$q, entityNames:$e,
 page:{index:0,size:5}){ hits{id name entity} } }"""
OT_PREFIX = {"MONDO": "MONDO", "ORPHA": "Orphanet", "ORPHANET": "Orphanet", "EFO": "EFO"}


def _stage(s) -> str:
    return str(s or "").replace("_", " ").lower() or "unknown stage"


def _clean(names) -> list[str]:
    """query_hygiene: names sanitised for quoted query terms (context.sanitize_terms)."""
    names = [n for n in names or [] if n]
    return sanitize_terms(names) if improvements.on("query_hygiene") else names


class Registries(Provider):
    name = "registries"
    throttle = Throttle(0.3)  # ClinicalTrials.gov allows ~50 requests/minute

    # -- HTTP --------------------------------------------------------------------
    def _live(self, url: str, params: dict) -> dict:
        """GET JSON, cached, bypassing the local indexes (for ClinicalTrials.gov)."""
        k = Cache.key("GET", url, params, None)
        body = self.cache.get(k)
        if body is None:
            self.cache.misses += 1
            self.throttle.wait()
            r = self.session.get(url, params=params, timeout=60)
            r.raise_for_status()
            body = r.text
            self.cache.put(k, url, body)
        else:
            self.cache.hits += 1
        return json.loads(body)

    def _graphql(self, query: str, variables: dict) -> dict:
        payload = {"query": query, "variables": variables}
        reply = _local.route("POST", OT, json_body=payload)
        if reply is not None and reply.status < 400:
            return (json.loads(reply.text()) or {}).get("data") or {}
        k = Cache.key("POST", OT, None, payload)
        body = self.cache.get(k)
        if body is None:
            self.cache.misses += 1
            r = self.session.post(OT, json=payload, timeout=60)
            r.raise_for_status()
            body = r.text
            self.cache.put(k, OT, body)
        else:
            self.cache.hits += 1
        return (json.loads(body) or {}).get("data") or {}

    # -- ClinicalTrials.gov --------------------------------------------------------
    @staticmethod
    def _trial(s: dict) -> dict:
        p = s.get("protocolSection") or {}
        ident = p.get("identificationModule") or {}
        ints = []
        for i in (p.get("armsInterventionsModule") or {}).get("interventions") or []:
            # "GLM101 (Part A, Double-blind)" -> "GLM101": arms of one drug are one node
            name = re.sub(r"\s*\((part|arm|cohort|group|stage|period|phase)\b[^)]*\)\s*$", "",
                          (i.get("name") or "").strip(), flags=re.I)
            if not name or NOT_SOLUTION.match(name) or i.get("type") not in CT_TYPES:
                continue
            ints.append({"name": name, "type": CT_TYPES[i["type"]],
                         "other_names": [x for x in i.get("otherNames") or [] if x][:5]})
        cm = p.get("conditionsModule") or {}
        return {"nct": ident.get("nctId"), "title": ident.get("briefTitle") or "",
                "status": _stage((p.get("statusModule") or {}).get("overallStatus")),
                "phases": [_stage(x) for x in (p.get("designModule") or {}).get("phases") or []],
                "type": (p.get("designModule") or {}).get("studyType"),
                "start": ((p.get("statusModule") or {}).get("startDateStruct") or {}).get("date"),
                "conditions": cm.get("conditions") or [], "interventions": ints,
                "summary": ((p.get("descriptionModule") or {}).get("briefSummary") or "")[:600]}

    def disease_trials(self, profile, size: int = 100) -> list[dict]:
        """Interventional trials of the input disease, matched by MeSH id or by its names
        and kept only when their own conditions / title name the disease."""
        d = profile.disease
        names = _clean(d.get("names") or [d["label"]])
        names = [n for n in names if len(key(n)) > 3][:6]
        mesh = next((x.split(":", 1)[1] for x in d.get("xrefs") or []
                     if x.upper().startswith("MESH:D")), None)
        queries = [{"query.term": f"AREA[ConditionMeshId]{mesh}"}] if mesh else []
        if names:
            queries.append({"query.cond": " OR ".join(f'"{n}"' for n in names)})
        out: dict[str, dict] = {}
        keys = {key(n) for n in names}
        for q in queries:
            try:
                rows = self._live(CTGOV, {**q, "pageSize": size, "fields": CT_FIELDS,
                                          "format": "json"}).get("studies") or []
            except Exception as e:
                self.fail(f"ctgov {q}", e)
                continue
            for s in rows:
                t = self._trial(s)
                text = " ".join(key(x) for x in [*t["conditions"], t["title"]])
                if t["nct"] and (q.get("query.term") or any(k in text for k in keys)):
                    out.setdefault(t["nct"], t)
        return list(out.values())

    def trials_of(self, drug_names: list[str], profile) -> list[dict]:
        """Trials registering one of the drug's names as intervention for the disease."""
        names = [n for n in dict.fromkeys(_clean(drug_names)) if len(key(n)) > 2
                 and not is_short(n)][:3]
        dn = [n for n in _clean(profile.disease.get("names") or []) if len(key(n)) > 3
              and not is_short(n)][:4]
        if not names or not dn:
            return []
        params = {"query.cond": " OR ".join(f'"{n}"' for n in dn),
                  "query.intr": " OR ".join(f'"{n}"' for n in names),
                  "pageSize": 20, "fields": CT_FIELDS, "format": "json"}
        try:
            return [self._trial(s) for s in self._live(CTGOV, params).get("studies") or []]
        except Exception as e:
            self.fail(f"ctgov trials of {names[0]}", e)
            return []

    # -- Open Targets / ChEMBL ------------------------------------------------------
    def disease_drugs(self, profile) -> list[dict]:
        """Open Targets drug / clinical candidates of the input disease."""
        d = profile.disease
        ids = []
        for c in [d["id"], *(d.get("xrefs") or [])]:
            p, _, local = c.partition(":")
            if p.upper() in OT_PREFIX:
                ids.append(f"{OT_PREFIX[p.upper()]}_{local}")
        for oid in ids:
            try:
                dis = self._graphql(OT_DISEASE_Q, {"id": oid}).get("disease")
            except Exception as e:
                self.fail(f"opentargets disease {oid}", e)
                continue
            if dis:
                rows = (dis.get("drugAndClinicalCandidates") or {}).get("rows") or []
                return [{"id": f"ChEMBL:{r['drug']['id']}", "name": r["drug"]["name"],
                         "stage": _stage(r.get("maxClinicalStage")), "source": oid}
                        for r in rows if r.get("drug")]
        return []

    def _search(self, q: str, entity: str) -> list[dict]:
        try:
            return ((self._graphql(OT_SEARCH_Q, {"q": q, "e": [entity]}).get("search") or {})
                    .get("hits") or [])
        except Exception as e:
            self.fail(f"opentargets search {q}", e)
            return []

    def target_drugs(self, symbol: str) -> list[dict]:
        """Drugs and clinical candidates acting on a gene's product (Open Targets)."""
        hit = next((h for h in self._search(symbol, "target")
                    if key(h.get("name")) == key(symbol)), None)
        if not hit:
            return []
        try:
            t = self._graphql(OT_TARGET_Q, {"id": hit["id"]}).get("target") or {}
        except Exception as e:
            self.fail(f"opentargets target {symbol}", e)
            return []
        rows = (t.get("drugAndClinicalCandidates") or {}).get("rows") or []
        return [{"id": f"ChEMBL:{r['drug']['id']}", "name": r["drug"]["name"],
                 "stage": _stage(r.get("maxClinicalStage")), "target": t.get("approvedSymbol")
                 or symbol, "source": "Open Targets"} for r in rows if r.get("drug")]

    def drug_mechanisms(self, name: str) -> dict | None:
        """{id, name, mechanisms: [{action, text, targets}], indications} by drug name:
        Open Targets first, ChEMBL for compounds Open Targets does not have."""
        hit = next((h for h in self._search(name, "drug")
                    if key(h.get("name")) == key(name)), None)
        if hit:
            try:
                d = self._graphql(OT_DRUG_Q, {"id": hit["id"]}).get("drug") or {}
            except Exception as e:
                self.fail(f"opentargets drug {name}", e)
                d = {}
            if d:
                return {"id": f"ChEMBL:{d['id']}", "name": d.get("name") or name,
                        "source": "Open Targets",
                        "mechanisms": [{"action": m.get("actionType"),
                                        "text": m.get("mechanismOfAction"),
                                        "targets": [t.get("approvedSymbol") for t in
                                                    m.get("targets") or []]}
                                       for m in (d.get("mechanismsOfAction") or {}).get("rows")
                                       or []],
                        "indications": [{"disease": r["disease"]["name"],
                                         "stage": _stage(r.get("maxClinicalStage"))}
                                        for r in (d.get("indications") or {}).get("rows") or []
                                        if r.get("disease")][:8]}
        return self._chembl(name)

    def _chembl(self, name: str) -> dict | None:
        try:
            mols = self.fetch_json(f"{CHEMBL}/molecule/search.json",
                                   {"q": name, "limit": 3}).get("molecules") or []
        except Exception as e:
            self.fail(f"chembl search {name}", e)
            return None
        want = key(name)
        mol = next((m for m in mols if key(m.get("pref_name")) == want or any(
            key(s.get("molecule_synonym")) == want for s in m.get("molecule_synonyms") or [])),
            None)
        if not mol:
            return None
        cid = mol["molecule_chembl_id"]
        try:
            mechs = self.fetch_json(f"{CHEMBL}/mechanism.json",
                                    {"molecule_chembl_id": cid, "limit": 20}
                                    ).get("mechanisms") or []
        except Exception as e:
            self.fail(f"chembl mechanism {cid}", e)
            mechs = []
        return {"id": f"ChEMBL:{cid}", "name": mol.get("pref_name") or name, "source": "ChEMBL",
                "mechanisms": [{"action": m.get("action_type"),
                                "text": m.get("mechanism_of_action"),
                                "targets": [m.get("target_chembl_id")]} for m in mechs],
                "indications": []}

    # -- Europe PMC -----------------------------------------------------------------
    def cooccurrence(self, a_names: list[str], b_names: list[str], n: int = 5,
                     also: list[str] = ()) -> dict:
        """{count, papers (PMIDs / ids)} of papers naming one of a_names AND one of b_names
        (AND one of `also`, when given) in title or abstract."""
        def ors(names):
            ns = [x.replace('"', "") for x in dict.fromkeys(_clean(names))
                  if len(key(x)) > 2 and not is_short(x)][:6]
            return "(" + " OR ".join(f'TITLE_ABS:"{x}"' for x in ns) + ")" if ns else ""
        a, b = ors(a_names), ors(b_names)
        if not a or not b:
            return {"count": None, "papers": [], "query": ""}
        q = f"{a} AND {b}"
        if also and ors(also):
            q += f" AND {ors(also)}"
        try:
            d = self.fetch_json(EPMC, {"query": q, "format": "json", "pageSize": n,
                                       "resultType": "lite"})
        except Exception as e:
            self.fail(f"europepmc count {q[:80]}", e)
            return {"count": None, "papers": [], "query": q}
        rows = (d.get("resultList") or {}).get("result") or []
        return {"count": d.get("hitCount"), "query": q,
                "papers": [f"PMID:{r['pmid']}" if r.get("pmid") else r.get("id") for r in rows]}


# -- records for build.Graph --------------------------------------------------------
def registry_records(reg: Registries, profile, normalizer) -> tuple[list[dict], dict]:
    """(paper-like records of the disease's registered trials and Open Targets drug
    candidates, raw data for the JSON output). input_identity: also those of the input's
    unqualified parents (profile.identity: "Rett syndrome" for "Atypical Rett syndrome"),
    recorded on the parent's node, which build.Graph counts as the input."""
    targets = [profile.disease]
    if improvements.on("input_identity"):
        targets += getattr(profile, "identity", None) or []
    records, all_trials, all_cands, seen = [], [], [], set()
    for d in targets:
        rec, trials, cands = _records_for(reg, SimpleNamespace(disease=d), normalizer, seen)
        records += rec
        all_trials += trials
        all_cands += cands
    return records, {"trials": all_trials, "open_targets_candidates": all_cands,
                     "errors": reg.errors[:20]}


def _records_for(reg: Registries, profile, normalizer, seen: set) -> tuple[list, list, list]:
    """(records, trials, Open Targets candidates) of one disease; trials whose NCT id is in
    `seen` are skipped (and new ones added to it)."""
    d = profile.disease
    dent = {"name": d["label"], "type": "disease", "synonyms": [], "expanded": d["label"],
            "defined": True}
    dnorm = {"id": d["id"], "label": d["label"], "how": "graph", "xrefs": []}
    records = []
    trials = [t for t in reg.disease_trials(profile) if t["nct"] not in seen]
    seen |= {t["nct"] for t in trials}
    for t in trials:
        ents = {f"i{j}": {"name": i["name"], "type": i["type"], "synonyms": i["other_names"],
                          "expanded": i["name"], "defined": True}
                for j, i in enumerate(t["interventions"])}
        if not ents or t["type"] != "INTERVENTIONAL":
            continue
        norms = normalizer.resolve_many(ents, title=t["title"])
        phase = "/".join(t["phases"]) or t["type"] or ""
        quote = f"{t['title']} ({phase}; {t['status']})"
        records.append({
            "key": f"NCT:{t['nct']}", "origin": "registry", "text_source": "registry",
            "truncated": False, "chars": 0, "summary": t["summary"],
            "meta": {"title": t["title"], "year": (t["start"] or "")[:4] or None,
                     "journal": "ClinicalTrials.gov", "nct": t["nct"],
                     "url": f"https://clinicaltrials.gov/study/{t['nct']}"},
            "entities": {"d": {**dent, "norm": dnorm},
                         **{k: {**e, "norm": norms[k]} for k, e in ents.items()}},
            "edges": [{"subject": k, "predicate": "tested_in", "object": "d", "effect": "na",
                       "evidence_level": "registered_trial", "organism": "human",
                       "context_disease": "d", "context_model": "",
                       "evidence": [{"passage": t["nct"], "section": "registry",
                                     "quote": quote}]} for k in ents],
            "stats": {"edges_kept": len(ents)}})
    cands = reg.disease_drugs(profile)
    if cands:
        ents, edges = {"d": {**dent, "norm": dnorm}}, []
        for j, c in enumerate(cands):
            k = f"c{j}"
            e = {"name": c["name"], "type": "drug", "synonyms": [], "expanded": c["name"],
                 "defined": True}
            norm = normalizer.resolve(c["name"], "drug")
            if norm["how"] == "text":
                norm = {"id": c["id"], "label": c["name"], "how": "database", "xrefs": []}
            ents[k] = {**e, "norm": norm}
            edges.append({"subject": k, "predicate": "tested_in", "object": "d", "effect": "na",
                          "evidence_level": "registered_trial", "organism": "human",
                          "context_disease": "d", "context_model": "",
                          "evidence": [{"passage": c["id"], "section": "Open Targets",
                                        "quote": f"Open Targets lists {c['name']} as a drug / "
                                                 f"clinical candidate for {d['label']} "
                                                 f"(max stage: {c['stage']})"}]})
        records.append({"key": f"OT:{c['source']}", "origin": "registry",
                        "text_source": "registry", "truncated": False, "chars": 0,
                        "summary": "", "meta": {"title": "Open Targets drug and clinical "
                                                         f"candidates for {d['label']}",
                                                "journal": "Open Targets Platform"},
                        "entities": ents, "edges": edges, "stats": {"edges_kept": len(edges)}})
    return records, trials, cands


def target_drug_records(reg: Registries, genes: list[dict], normalizer,
                        per_gene: int = 15) -> tuple[list[dict], list[dict]]:
    """Records "<drug> targets <gene>" (evidence level "database") for the mechanism genes,
    from Open Targets' drug / clinical candidates of each gene product."""
    records, raw = [], []
    for g in genes:
        rows = reg.target_drugs(g["label"])[:per_gene]
        raw.append({"gene": g["id"], "symbol": g["label"], "drugs": rows})
        if not rows:
            continue
        ents = {"g": {"name": g["label"], "type": "gene", "synonyms": [], "expanded": g["label"],
                      "defined": True, "norm": {"id": g["id"], "label": g["label"],
                                                "how": "graph", "xrefs": []}}}
        edges = []
        for j, r in enumerate(rows):
            k = f"c{j}"
            norm = normalizer.resolve(r["name"], "drug")
            if norm["how"] == "text":
                norm = {"id": r["id"], "label": r["name"], "how": "database", "xrefs": []}
            ents[k] = {"name": r["name"], "type": "drug", "synonyms": [], "expanded": r["name"],
                       "defined": True, "norm": norm}
            edges.append({"subject": k, "predicate": "targets", "object": "g", "effect": "na",
                          "evidence_level": "database", "organism": "",
                          "context_disease": "", "context_model": "",
                          "evidence": [{"passage": r["id"], "section": "Open Targets",
                                        "quote": f"Open Targets / ChEMBL: {r['name']} acts on "
                                                 f"{r['target']} (max clinical stage: "
                                                 f"{r['stage']})"}]})
        records.append({"key": f"OT:target:{g['label']}", "origin": "registry",
                        "text_source": "registry", "truncated": False, "chars": 0,
                        "summary": "", "meta": {"title": f"Open Targets drugs acting on "
                                                         f"{g['label']}",
                                                "journal": "Open Targets Platform"},
                        "entities": ents, "edges": edges, "stats": {"edges_kept": len(edges)}})
    return records, raw
