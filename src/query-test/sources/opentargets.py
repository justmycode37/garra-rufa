"""Open Targets Platform GraphQL API (https://api.platform.opentargets.org), no auth.

One GraphQL request per entity pulls every relation the platform has for it:
  target (ENSEMBL id; HGNC / gene symbol / DrugBank mapped with mapIds)
      associated_with (diseases, by overall score), targeted_by_drug (drug and clinical
      candidates), in_pathway (Reactome), protein_interacts_with (IntAct, STRING, SIGNOR,
      Reactome), has_function / involved_in / located_in (GO by aspect),
      located_in (UniProt subcellular), expressed_in (baseline expression tissues and
      cell types), mouse_phenotype (MGI/IMPC), safety_liability, chemical_probe,
      ortholog_of / paralog_of, pharmacogenomic_drug / _variant, in_target_class,
      tractable_by (tractability buckets that are true)
  disease (MONDO / EFO / ORPHA / HP / OTAR id)
      subclass_of / has_subclass, in_therapeutic_area, located_in (anatomy),
      associated_target, treated_by (drug and clinical candidates), has_phenotype
  drug (ChEMBL id; DrugBank mapped with mapIds)
      <action type>_of targets (mechanisms of action), indicated_for (indications with
      clinical stage), adverse_event (FAERS, MedDRA), warning (toxicity warnings),
      parent_molecule / child_molecule
Each record's cross-references (HGNC, UniProt, DrugBank, OMIM, Orphanet, ...) are put on
the node so other sources' nodes merge with it. Free text: OT search ("matches").

Extra data (Node.info): disease description + exact synonyms, target function
descriptions, drug description, and the platform page url. Disease -> associated_target
edges carry the PMIDs of OT's Europe PMC text-mining evidence for that pair
(Edge.evidence; one extra request per disease, best-scored evidence first).
"""
import re

from ._ols import interleave, slug
from .base import Edge, Node, Source, info

API = "https://api.platform.opentargets.org/api/v4/graphql"
PAGE = "https://platform.opentargets.org/{}/{}"
EVIDENCE_PER_TARGET = 10  # PMIDs kept per disease-target edge
# OT disease id prefix <-> project CURIE prefix
OT_TO_CURIE = {"Orphanet": "ORPHA", "MONDO": "MONDO", "EFO": "EFO", "HP": "HP",
               "OTAR": "OTAR", "GO": "GO", "UBERON": "UBERON", "CL": "CL", "MP": "MP",
               "NCIT": "NCIT", "DOID": "DOID"}
CURIE_TO_OT = {"ORPHA": "Orphanet", "ORPHANET": "Orphanet", "MONDO": "MONDO", "EFO": "EFO",
               "HP": "HP", "OTAR": "OTAR"}
XREF_PREFIX = {"Orphanet": "ORPHA", "icd11.foundation": "ICD11",
               "drugbank": "DrugBank", "uniprot_swissprot": "UniProtKB",
               "uniprot_trembl": "UniProtKB"}
GO_REL = {"F": "has_function", "P": "involved_in", "C": "located_in"}
GO_KIND = {"F": "function", "P": "process", "C": "component"}

TARGET_Q = """query($id:String!,$n:Int!){ target(ensemblId:$id){ id approvedSymbol
 functionDescriptions
 dbXrefs{id source} proteinIds{id source}
 associatedDiseases(page:{index:0,size:$n}){rows{score disease{id name}}}
 drugAndClinicalCandidates{rows{maxClinicalStage drug{id name}}}
 pathways{pathwayId pathway}
 interactions(page:{index:0,size:$n}){rows{score sourceDatabase targetB{id approvedSymbol}}}
 geneOntology{aspect term{id label}}
 subcellularLocations{location termSL}
 baselineExpression(page:{index:0,size:$n}){rows{median tissueBiosample{biosampleId biosampleName}
   celltypeBiosample{biosampleId biosampleName}}}
 mousePhenotypes{modelPhenotypeId modelPhenotypeLabel}
 safetyLiabilities{event eventId}
 chemicalProbes{id drugId isHighQuality}
 homologues{speciesName targetGeneId targetGeneSymbol homologyType}
 pharmacogenomics{variantRsId drugs{drugId drugFromSource}}
 targetClass{id label}
 tractability{label modality value}
}}"""
DISEASE_Q = """query($id:String!,$n:Int!){ disease(efoId:$id){ id name dbXRefs
 description synonyms{relation terms}
 parents{id name} children{id name} therapeuticAreas{id name} directLocations{id name}
 associatedTargets(page:{index:0,size:$n}){rows{score target{id approvedSymbol}}}
 drugAndClinicalCandidates{rows{maxClinicalStage drug{id name}}}
 phenotypes(page:{index:0,size:$n}){rows{phenotypeHPO{id name} evidence{qualifierNot}}}
}}"""
DRUG_Q = """query($id:String!,$n:Int!){ drug(chemblId:$id){ id name description
 crossReferences{source ids} parentMolecule{id name} childMolecules{id name}
 mechanismsOfAction{rows{actionType targets{id approvedSymbol}}}
 indications{rows{maxClinicalStage disease{id name}}}
 adverseEvents(page:{index:0,size:$n}){rows{name meddraCode}}
 drugWarnings{warningType efoId efoTerm toxicityClass}
}}"""
SEARCH_Q = """query($q:String!){ search(queryString:$q, entityNames:["target","disease","drug"],
 page:{index:0,size:25}){ hits{id name entity} } }"""
LIT_Q = """query($id:String!,$t:[String!]!){ disease(efoId:$id){
 evidences(ensemblIds:$t, datasourceIds:["europepmc"], size:500){ rows{ target{id} literature } } } }"""
MAP_Q = """query($t:[String!]!,$e:[String!]!){ mapIds(queryTerms:$t, entityNames:$e){
 mappings{ term hits{id name entity} } } }"""


# maxClinicalStage, most advanced first; anything else (UNKNOWN, ...) sorts last
STAGES = ["APPROVAL", "PHASE_4", "PHASE_3", "PHASE_2_3", "PHASE_2", "PHASE_1_2", "PHASE_1",
          "EARLY_PHASE_1"]


def _stage_rank(stage: str | None) -> int:
    return STAGES.index(stage) if stage in STAGES else len(STAGES)


def _stage_relation(stage: str | None) -> str:
    """approved_drug for drugs approved for the disease, else trial_drug_phase_<n> (or
    trial_drug when the stage is unknown): OpenTargets' list includes every clinical
    candidate, so "treated_by" overstated it (lenograstim for stiff person syndrome)."""
    if stage == "APPROVAL":
        return "approved_drug"
    if stage in STAGES:
        return "trial_drug_" + stage.lower()
    return "trial_drug"


def _curie(ot_id: str) -> str:
    """MONDO_0007947 -> MONDO:0007947, Orphanet_558 -> ORPHA:558, UBERON_0000007 -> ..."""
    p, sep, local = ot_id.partition("_")
    if sep and p in OT_TO_CURIE:
        return f"{OT_TO_CURIE[p]}:{local}"
    return ot_id.replace("_", ":", 1) if "_" in ot_id else f"OT:{ot_id}"


def _xref(c: str) -> str:
    p, _, local = c.partition(":")
    return f"{XREF_PREFIX.get(p, p)}:{local}"


class OpenTargetsSource(Source):
    name = "opentargets"
    id_prefixes = frozenset({"ENSEMBL", "HGNC", "SYMBOL", *CURIE_TO_OT, "CHEMBL", "DRUGBANK"})
    by_name = True

    def query(self, node: Node, limit: int = 10) -> list[Edge]:
        try:
            return self._query(node, limit)
        except Exception:
            return []

    def _gql(self, query: str, **variables):
        r = self.session.post(API, json={"query": query, "variables": variables}, timeout=60)
        r.raise_for_status()
        return r.json().get("data") or {}

    def _query(self, node: Node, limit: int) -> list[Edge]:
        if node.id is None:
            return self._search(node, limit)
        n = max(limit, 10)
        for c in self.ids_for(node):
            p, _, local = c.partition(":")
            p = p.upper()
            edges: list[Edge] = []
            if p == "ENSEMBL" and local.startswith("ENSG"):
                edges = self._target(node, local.split(".")[0], n, limit)
            elif p in CURIE_TO_OT:
                edges = self._disease(node, f"{CURIE_TO_OT[p]}_{local}", n, limit)
            elif p == "CHEMBL":
                edges = self._drug(node, local if local.upper().startswith("CHEMBL")
                                   else f"CHEMBL{local}", n, limit)
            elif p in ("HGNC", "SYMBOL", "DRUGBANK"):
                term = {"HGNC": f"HGNC:{local}", "SYMBOL": local, "DRUGBANK": local}[p]
                hit = self._map(term, "drug" if p == "DRUGBANK" else "target")
                if hit and hit["entity"] == "target":
                    edges = self._target(node, hit["id"], n, limit)
                elif hit:
                    edges = self._drug(node, hit["id"], n, limit)
            if edges:
                return edges
        return []

    def _map(self, term: str, entity: str) -> dict | None:
        d = self._gql(MAP_Q, t=[term], e=[entity])
        for m in d.get("mapIds", {}).get("mappings") or []:
            hits = m.get("hits") or []
            # a symbol must map exactly (mapIds also matches synonyms / old symbols)
            exact = [h for h in hits if entity != "target" or ":" in term
                     or h["name"].upper() == term.upper()]
            if exact:
                return exact[0]
        return None

    def _search(self, node: Node, limit: int) -> list[Edge]:
        hits = self._gql(SEARCH_Q, q=node.label).get("search", {}).get("hits") or []
        low = node.label.lower()
        hits = [h for h in hits if low in h["name"].lower()] or hits[:1]
        return [Edge(node, self._entity(h["entity"], h["id"], h["name"]), "matches", self.name)
                for h in hits[:limit]]

    # -- node builders -----------------------------------------------------
    def _entity(self, entity: str, ot_id: str, name: str) -> Node:
        if entity == "target":
            return self._gene(ot_id, name)
        if entity == "drug":
            return self._chem(ot_id, name)
        return self._dis(ot_id, name)

    def _gene(self, ens: str, symbol: str | None) -> Node:
        return Node(symbol or ens, f"ENSEMBL:{ens}", "gene", self.name)

    def _chem(self, chembl: str, name: str | None) -> Node:
        return Node(name or chembl, f"ChEMBL:{chembl}", "drug", self.name)

    def _dis(self, ot_id: str, name: str | None, kind: str = "disease") -> Node:
        cur = _curie(ot_id)
        if cur.startswith("HP:"):
            kind = "phenotype"
        return Node(name or cur, cur, kind, self.name)

    def _src(self, node: Node, ot_curie: str, label: str, kind: str, xrefs,
             extra: dict | None = None) -> Node:
        return Node(node.label if node.id else label, node.id or ot_curie, kind, node.source,
                    tuple(dict.fromkeys((*node.xrefs, ot_curie, *xrefs))), extra or {})

    def _literature(self, ot_id: str, targets: list[str]) -> dict[str, tuple[str, ...]]:
        """target ENSG -> PMIDs of the Europe PMC evidence for (disease, target)."""
        if not targets:
            return {}
        try:
            rows = (self._gql(LIT_Q, id=ot_id, t=targets).get("disease") or {})                 .get("evidences", {}).get("rows") or []
        except Exception:
            return {}
        out: dict[str, list[str]] = {}
        for r in rows:  # ordered by score
            lst = out.setdefault((r.get("target") or {}).get("id"), [])
            for pm in r.get("literature") or []:
                if len(lst) < EVIDENCE_PER_TARGET and f"PMID:{pm}" not in lst:
                    lst.append(f"PMID:{pm}")
        return {k: tuple(v) for k, v in out.items()}

    # -- target --------------------------------------------------------------
    def _target(self, node: Node, ens: str, n: int, limit: int) -> list[Edge]:
        t = self._gql(TARGET_Q, id=ens, n=n).get("target")
        if not t:
            return []
        xr = [f"HGNC:{x['id']}" for x in t.get("dbXrefs") or [] if x["source"] == "HGNC"]
        xr += [f"UniProtKB:{p['id']}" for p in t.get("proteinIds") or []
               if p["source"] == "uniprot_swissprot"]
        src = self._src(node, f"ENSEMBL:{ens}", t["approvedSymbol"], "gene", xr,
                        info(description=" ".join(t.get("functionDescriptions") or []),
                             url=PAGE.format("target", ens)))

        def mk(rel, dst):
            return Edge(src, dst, rel, self.name)

        g: list[list[Edge]] = []
        g.append([mk("associated_with", self._dis(r["disease"]["id"], r["disease"]["name"]))
                  for r in (t.get("associatedDiseases") or {}).get("rows") or []])
        g.append([mk("targeted_by_drug", self._chem(r["drug"]["id"], r["drug"]["name"]))
                  for r in (t.get("drugAndClinicalCandidates") or {}).get("rows") or []
                  if r.get("drug")])
        g.append([mk("in_pathway", Node(p["pathway"], f"Reactome:{p['pathwayId']}", "pathway",
                                        self.name)) for p in t.get("pathways") or []])
        rows = sorted((t.get("interactions") or {}).get("rows") or [],
                      key=lambda r: -(r.get("score") or 0))
        g.append([mk("protein_interacts_with", self._gene(r["targetB"]["id"],
                                                          r["targetB"]["approvedSymbol"]))
                  for r in rows if r.get("targetB") and r["targetB"]["id"] != ens])
        for aspect, rel in GO_REL.items():
            g.append([mk(rel, Node(x["term"]["label"], x["term"]["id"], GO_KIND[aspect],
                                   self.name))
                      for x in t.get("geneOntology") or [] if x["aspect"] == aspect])
        g.append([mk("located_in", Node(x["location"], f"UniProtSL:{x['termSL']}",
                                        "component", self.name))
                  for x in t.get("subcellularLocations") or [] if x.get("termSL")])
        expr = sorted((t.get("baselineExpression") or {}).get("rows") or [],
                      key=lambda r: -(r.get("median") or 0))
        ex = []
        for r in expr:
            for b in (r.get("tissueBiosample"), r.get("celltypeBiosample")):
                if b and b.get("biosampleId"):
                    cur = _curie(b["biosampleId"])
                    ex.append(mk("expressed_in", Node(b["biosampleName"], cur,
                                                      "cell" if cur.startswith("CL:")
                                                      else "anatomy", self.name)))
        g.append(ex)
        g.append([mk("mouse_phenotype", Node(m["modelPhenotypeLabel"], m["modelPhenotypeId"],
                                             "phenotype", self.name))
                  for m in t.get("mousePhenotypes") or []])
        g.append([mk("safety_liability",
                     Node(s["event"], _curie(s["eventId"]) if s.get("eventId")
                          else f"OT:safety/{slug(s['event'])}", "phenotype", self.name))
                  for s in t.get("safetyLiabilities") or [] if s.get("event")])
        g.append([mk("chemical_probe", self._chem(p["drugId"], p["id"]) if p.get("drugId")
                     else Node(p["id"], f"OT:probe/{slug(p['id'])}", "drug", self.name))
                  for p in sorted(t.get("chemicalProbes") or [],
                                  key=lambda p: not p.get("isHighQuality"))])
        homs = sorted(t.get("homologues") or [], key=lambda h: h["speciesName"] != "Human")
        g.append([mk("paralog_of" if "paralog" in h["homologyType"] else "ortholog_of",
                     Node((h["targetGeneSymbol"] or h["targetGeneId"]) + (
                         "" if h["speciesName"] == "Human" else f" ({h['speciesName']})"),
                          f"ENSEMBL:{h['targetGeneId']}", "gene", self.name))
                  for h in homs if h.get("targetGeneId") and h["targetGeneId"] != ens])
        pgx_d, pgx_v = [], []
        for p in t.get("pharmacogenomics") or []:
            for dr in p.get("drugs") or []:
                if dr.get("drugId"):
                    pgx_d.append(mk("pharmacogenomic_drug",
                                    self._chem(dr["drugId"], dr.get("drugFromSource"))))
            if p.get("variantRsId"):
                pgx_v.append(mk("pharmacogenomic_variant",
                                Node(p["variantRsId"], f"dbSNP:{p['variantRsId']}", "variant",
                                     self.name)))
        g += [pgx_d, pgx_v]
        g.append([mk("in_target_class", Node(c["label"], f"ChEMBL.targetclass:{c['id']}",
                                             "protein_class", self.name))
                  for c in t.get("targetClass") or []])
        g.append([mk("tractable_by", Node(f"{x['modality']}: {x['label']}",
                                          f"OT:tractability/{x['modality']}/{slug(x['label'])}",
                                          "tractability", self.name))
                  for x in t.get("tractability") or [] if x.get("value")])
        return interleave(g, limit)

    # -- disease -------------------------------------------------------------
    def _disease(self, node: Node, ot_id: str, n: int, limit: int) -> list[Edge]:
        d = self._gql(DISEASE_Q, id=ot_id, n=n).get("disease")
        if not d:
            return []
        xr = [_xref(x) for x in d.get("dbXRefs") or [] if re.match(r"^[\w.]+:\S+$", x)]
        kind = "phenotype" if ot_id.startswith("HP_") else "disease"
        syn = [x for s in d.get("synonyms") or [] if s.get("relation") == "hasExactSynonym"
               for x in s.get("terms") or []]
        src = self._src(node, _curie(ot_id), d["name"], kind, xr,
                        info(description=d.get("description"), synonyms=syn,
                             url=PAGE.format("disease", ot_id)))

        def mk(rel, dst, evidence=()):
            return Edge(src, dst, rel, self.name, evidence)

        g: list[list[Edge]] = []
        for key, rel in (("parents", "subclass_of"), ("children", "has_subclass"),
                         ("therapeuticAreas", "in_therapeutic_area")):
            g.append([mk(rel, self._dis(x["id"], x["name"], kind)) for x in d.get(key) or []])
        g.append([mk("located_in", self._dis(x["id"], x["name"], "anatomy"))
                  for x in d.get("directLocations") or []])
        targets = (d.get("associatedTargets") or {}).get("rows") or []
        lit = self._literature(ot_id, [r["target"]["id"] for r in targets])
        g.append([mk("associated_target", self._gene(r["target"]["id"],
                                                     r["target"]["approvedSymbol"]),
                     lit.get(r["target"]["id"], ()))
                  for r in targets])
        drugs = [r for r in (d.get("drugAndClinicalCandidates") or {}).get("rows") or []
                 if r.get("drug")]
        drugs.sort(key=lambda r: _stage_rank(r.get("maxClinicalStage")))
        g.append([mk(_stage_relation(r.get("maxClinicalStage")),
                     self._chem(r["drug"]["id"], r["drug"]["name"])) for r in drugs])
        g.append([mk("has_phenotype", self._dis(r["phenotypeHPO"]["id"],
                                                r["phenotypeHPO"]["name"], "phenotype"))
                  for r in (d.get("phenotypes") or {}).get("rows") or []
                  if r.get("phenotypeHPO")
                  and not all(e.get("qualifierNot") for e in r.get("evidence") or [{}])])
        return interleave(g, limit)

    # -- drug ----------------------------------------------------------------
    def _drug(self, node: Node, chembl: str, n: int, limit: int) -> list[Edge]:
        d = self._gql(DRUG_Q, id=chembl, n=n).get("drug")
        if not d:
            return []
        xr = [f"{XREF_PREFIX.get(x['source'], x['source'])}:{i}"
              for x in d.get("crossReferences") or [] for i in x.get("ids") or []]
        src = self._src(node, f"ChEMBL:{chembl}", d["name"], "drug", xr,
                        info(description=d.get("description"),
                             url=PAGE.format("drug", chembl)))

        def mk(rel, dst):
            return Edge(src, dst, rel, self.name)

        g: list[list[Edge]] = []
        moa = []
        for r in (d.get("mechanismsOfAction") or {}).get("rows") or []:
            rel = f"{slug(r.get('actionType') or 'acts_on')}_of"
            moa += [mk(rel, self._gene(t["id"], t["approvedSymbol"])) for t in r.get("targets")
                    or []]
        g.append(moa)
        g.append([mk("indicated_for", self._dis(r["disease"]["id"], r["disease"]["name"]))
                  for r in (d.get("indications") or {}).get("rows") or [] if r.get("disease")])
        g.append([mk("adverse_event", Node(a["name"], f"MedDRA:{a['meddraCode']}" if a.get(
            "meddraCode") else f"OT:ae/{slug(a['name'])}", "phenotype", self.name))
                  for a in (d.get("adverseEvents") or {}).get("rows") or []])
        g.append([mk(slug(w.get("warningType") or "warning").removesuffix("_warning")
                     + "_warning",
                     self._dis(w["efoId"], w.get("efoTerm") or w.get("toxicityClass"),
                               "phenotype") if w.get("efoId")
                     else Node(w.get("toxicityClass") or "warning",
                               f"OT:toxicity/{slug(w.get('toxicityClass') or 'unknown')}",
                               "phenotype", self.name))
                  for w in d.get("drugWarnings") or []])
        if d.get("parentMolecule"):
            g.append([mk("parent_molecule", self._chem(d["parentMolecule"]["id"],
                                                       d["parentMolecule"]["name"]))])
        g.append([mk("child_molecule", self._chem(c["id"], c["name"]))
                  for c in d.get("childMolecules") or []])
        return interleave(g, limit)
