"""opentargets.sqlite from the Open Targets Platform parquet release, replacing the
GraphQL API (target / disease / drug / search / mapIds / evidences queries).

Per entity, the lists the GraphQL queries ask for are stored as JSON, already sorted:
  target(id, symbol, name, doc)            doc: functionDescriptions, dbXrefs, proteinIds,
                                           pathways, geneOntology, subcellularLocations,
                                           targetClass, safety, probes, homologues,
                                           tractability, mousePhenotypes, pharmacogenomics,
                                           interactions (top 100), baselineExpression (top 50)
  disease(id, name, doc)                   doc: description, dbXRefs, synonyms, parents,
                                           children, therapeuticAreas, phenotypes
  drug(id, name, doc)                      doc: description, crossReferences, parent,
                                           children, mechanisms, warnings, adverseEvents
  assoc(disease, target, score)            association_overall_direct
  candidate(drug, disease, target, stage)  clinical_indication / clinical_target
  lit(disease, target, score, pmid)        Europe PMC evidence, top 10 papers per pair
  alias(term, entity, id)                  mapIds: symbols, HGNC ids, DrugBank ids, names
  obsolete(old, id)                        disease ids merged into another one
"""

from __future__ import annotations

import glob
import json
import sys

from garra.local import RAW, finish, writer

DIR = RAW / "opentargets"
GO_ASPECT = {"biological_process": "P", "molecular_function": "F", "cellular_component": "C"}
EXPRESSION_PER_TARGET = 50
INTERACTIONS_PER_TARGET = 100
LIT_PER_PAIR = 10


def ready() -> bool:
    return all(glob.glob(str(DIR / d / "*.parquet")) for d in
               ("target", "disease", "drug_molecule", "association_overall_direct"))


def _files(ds: str) -> list[str]:
    return sorted(glob.glob(str(DIR / ds / "*.parquet")))


def _table(ds: str, columns=None):
    import pyarrow.parquet as pq
    fs = _files(ds)
    if not fs:
        return []
    return [r for f in fs for r in pq.read_table(f, columns=columns).to_pylist()]


def _clean(v):
    """parquet lists come back as lists; None -> []"""
    return v if v is not None else []


def build() -> None:
    import pandas as pd
    import pyarrow.parquet as pq

    con = writer("opentargets")
    con.executescript("""
    CREATE TABLE target(id TEXT PRIMARY KEY, symbol TEXT, name TEXT, doc TEXT);
    CREATE TABLE disease(id TEXT PRIMARY KEY, name TEXT, doc TEXT);
    CREATE TABLE drug(id TEXT PRIMARY KEY, name TEXT, doc TEXT);
    CREATE TABLE assoc(disease TEXT, target TEXT, score REAL);
    CREATE TABLE candidate(drug TEXT, disease TEXT, target TEXT, stage TEXT);
    CREATE TABLE lit(disease TEXT, target TEXT, score REAL, pmid TEXT);
    CREATE TABLE alias(term TEXT, entity TEXT, id TEXT);
    CREATE TABLE obsolete(old TEXT, id TEXT);
    """)

    go_label = {r["id"]: (r["label"], GO_ASPECT.get(r["namespace"]))
                for r in _table("go", ["id", "label", "namespace"])}
    biosample = {r["biosampleId"]: r["biosampleName"]
                 for r in _table("biosample", ["biosampleId", "biosampleName"])}

    # -- per-target side tables -------------------------------------------------------
    extra: dict[str, dict[str, list]] = {}

    def add(tid, key, val):
        if tid:
            extra.setdefault(tid, {}).setdefault(key, []).append(val)

    for r in _table("target_safety_event", ["targetId", "event", "eventId"]):
        add(r["targetId"], "safetyLiabilities", {"event": r["event"], "eventId": r["eventId"]})
    for r in _table("chemical_probe", ["targetId", "drugFromSource", "drugId", "isHighQuality"]):
        add(r["targetId"], "chemicalProbes", {"id": r["drugFromSource"], "drugId": r["drugId"],
                                              "isHighQuality": r["isHighQuality"]})
    for r in _table("target_tractability", ["targetId", "modality", "category", "value"]):
        add(r["targetId"], "tractability", {"label": r["category"], "modality": r["modality"],
                                            "value": r["value"]})
    for f in _files("homology"):
        for r in pq.read_table(f, columns=["targetId", "speciesName", "homologyType",
                                           "targetGeneId", "targetGeneSymbol"]).to_pylist():
            add(r["targetId"], "homologues", r)
    for r in _table("mouse_phenotype", ["targetFromSourceId", "modelPhenotypeId",
                                        "modelPhenotypeLabel"]):
        add(r["targetFromSourceId"], "mousePhenotypes",
            {"modelPhenotypeId": r["modelPhenotypeId"],
             "modelPhenotypeLabel": r["modelPhenotypeLabel"]})
    for r in _table("pharmacogenomics", ["targetFromSourceId", "variantRsId", "drugs"]):
        add(r["targetFromSourceId"], "pharmacogenomics",
            {"variantRsId": r["variantRsId"], "drugs": _clean(r["drugs"])})
    print("  side tables read", flush=True)

    # interactions: human-human, best score per partner, top N per target
    parts = []
    for f in _files("interaction"):
        df = pq.read_table(f, columns=["sourceDatabase", "targetA", "targetB", "scoring",
                                       "speciesB"]).to_pandas()
        df = df[df["targetB"].notna() & df["targetA"].notna()]
        df = df[df["speciesB"].map(lambda s: (s or {}).get("taxonId") == 9606)]
        parts.append(df[["sourceDatabase", "targetA", "targetB", "scoring"]])
    if parts:
        df = pd.concat(parts).sort_values(["targetA", "scoring"], ascending=[True, False])
        df = df.drop_duplicates(["targetA", "targetB"]).groupby("targetA").head(
            INTERACTIONS_PER_TARGET)
        for r in df.itertuples(index=False):
            add(r.targetA, "interactions", {"score": None if pd.isna(r.scoring)
                                            else float(r.scoring),
                                            "sourceDatabase": r.sourceDatabase,
                                            "targetB": r.targetB})
    print("  interactions done", flush=True)

    # baseline expression: top N rows by median per target
    parts = []
    for f in _files("baseline_expression"):
        df = pq.read_table(f, columns=["targetId", "tissueBiosampleId", "celltypeBiosampleId",
                                       "median"]).to_pandas()
        df = df[df["median"].notna()].sort_values(["targetId", "median"],
                                                    ascending=[True, False])
        parts.append(df.groupby("targetId").head(EXPRESSION_PER_TARGET))
    if parts:
        df = pd.concat(parts).sort_values(["targetId", "median"], ascending=[True, False])
        for r in df.groupby("targetId").head(EXPRESSION_PER_TARGET).itertuples(index=False):
            row = {"median": float(r.median)}
            for key, bid in (("tissueBiosample", r.tissueBiosampleId),
                             ("celltypeBiosample", r.celltypeBiosampleId)):
                if isinstance(bid, str) and bid:
                    row[key] = {"biosampleId": bid, "biosampleName": biosample.get(bid, bid)}
            add(r.targetId, "baselineExpression", row)
    print("  expression done", flush=True)

    # -- targets ----------------------------------------------------------------------
    rows, aliases = [], []
    for f in _files("target"):
        for t in pq.read_table(f, columns=[
                "id", "approvedSymbol", "approvedName", "functionDescriptions", "dbXrefs",
                "proteinIds", "pathways", "go", "subcellularLocations", "targetClass",
                "symbolSynonyms"]).to_pylist():
            tid = t["id"]
            gos = {}
            for g in _clean(t["go"]):
                lab, aspect = go_label.get(g["id"], (g["id"], g.get("aspect")))
                gos[(g["id"], aspect)] = {"aspect": g.get("aspect") or aspect,
                                          "term": {"id": g["id"], "label": lab}}
            doc = {"id": tid, "approvedSymbol": t["approvedSymbol"],
                   "approvedName": t["approvedName"],
                   "functionDescriptions": _clean(t["functionDescriptions"]),
                   "dbXrefs": _clean(t["dbXrefs"]), "proteinIds": _clean(t["proteinIds"]),
                   "pathways": [{"pathwayId": p["pathwayId"], "pathway": p["pathway"],
                                 "topLevelTerm": p.get("topLevelTerm")}
                                for p in _clean(t["pathways"])],
                   "geneOntology": list(gos.values()),
                   "subcellularLocations": [{"location": s["location"], "termSL": s.get("termSL"),
                                             "source": s.get("source")}
                                            for s in _clean(t["subcellularLocations"])],
                   "targetClass": [{"id": c["id"], "label": c["label"], "level": c["level"]}
                                   for c in _clean(t["targetClass"])],
                   **extra.get(tid, {})}
            rows.append((tid, t["approvedSymbol"], t["approvedName"], json.dumps(doc)))
            aliases.append((t["approvedSymbol"].upper(), "target", tid))
            for x in _clean(t["dbXrefs"]):
                if x["source"] == "HGNC":
                    aliases.append((f"HGNC:{x['id']}".upper(), "target", tid))
            for s in _clean(t["symbolSynonyms"]):
                aliases.append((s["label"].upper(), "target_synonym", tid))
    con.executemany("INSERT INTO target VALUES (?,?,?,?)", rows)
    print(f"  {len(rows)} targets", flush=True)

    # -- diseases ----------------------------------------------------------------------
    phen: dict[str, list] = {}
    for r in _table("disease_phenotype"):
        ev = [{"qualifierNot": e.get("qualifierNot"), "frequency": e.get("frequency"),
               "references": e.get("references") or []} for e in _clean(r["evidence"])]
        phen.setdefault(r["disease"], []).append({"phenotype": r["phenotype"], "evidence": ev})
    diseases = _table("disease", ["id", "name", "description", "dbXRefs", "parents", "children",
                                  "therapeuticAreas", "synonyms", "obsoleteTerms"])
    name_of = {d["id"]: d["name"] for d in diseases}
    rows, obs = [], []
    for d in diseases:
        syn = d["synonyms"] or {}
        doc = {"id": d["id"], "name": d["name"], "description": d["description"],
               "dbXRefs": _clean(d["dbXRefs"]),
               "synonyms": [{"relation": k, "terms": v} for k, v in syn.items() if v],
               "parents": [{"id": p, "name": name_of.get(p, p)} for p in _clean(d["parents"])],
               "children": [{"id": c, "name": name_of.get(c, c)} for c in _clean(d["children"])],
               "therapeuticAreas": [{"id": a, "name": name_of.get(a, a)}
                                    for a in _clean(d["therapeuticAreas"])],
               "directLocations": [],
               "phenotypes": [{"phenotypeHPO": {"id": p["phenotype"],
                                                "name": name_of.get(p["phenotype"],
                                                                    p["phenotype"])},
                               "evidence": p["evidence"]} for p in phen.get(d["id"], [])]}
        rows.append((d["id"], d["name"], json.dumps(doc)))
        aliases.append((d["name"].upper(), "disease", d["id"]))
        obs += [(o, d["id"]) for o in _clean(d["obsoleteTerms"])]
    con.executemany("INSERT INTO disease VALUES (?,?,?)", rows)
    con.executemany("INSERT INTO obsolete VALUES (?,?)", obs)
    print(f"  {len(rows)} diseases", flush=True)

    # -- drugs ---------------------------------------------------------------------------
    moa: dict[str, list] = {}
    for r in _table("drug_mechanism_of_action", ["actionType", "chemblIds", "targets",
                                                 "mechanismOfAction"]):
        for c in _clean(r["chemblIds"]):
            moa.setdefault(c, []).append({"actionType": r["actionType"],
                                          "mechanismOfAction": r["mechanismOfAction"],
                                          "targets": _clean(r["targets"])})
    warn: dict[str, list] = {}
    for r in _table("drug_warning", ["chemblIds", "warningType", "efoId", "efoTerm",
                                     "toxicityClass"]):
        for c in _clean(r["chemblIds"]):
            warn.setdefault(c, []).append({k: r[k] for k in ("warningType", "efoId", "efoTerm",
                                                             "toxicityClass")})
    ae: dict[str, list] = {}
    for r in sorted(_table("openfda_significant_adverse_drug_reactions"),
                    key=lambda r: -(r["llr"] or 0)):
        ae.setdefault(r["chembl_id"], []).append({"name": r["event"],
                                                  "meddraCode": r["meddraCode"],
                                                  "count": r["count"], "logLR": r["llr"]})
    drugs = _table("drug_molecule", ["id", "name", "description", "parentId", "childChemblIds",
                                     "crossReferences", "synonyms", "tradeNames", "drugType",
                                     "maximumClinicalStage"])
    dname = {d["id"]: d["name"] for d in drugs}
    rows = []
    for d in drugs:
        doc = {"id": d["id"], "name": d["name"], "description": d["description"],
               "drugType": d["drugType"], "maximumClinicalStage": d["maximumClinicalStage"],
               "crossReferences": _clean(d["crossReferences"]),
               "synonyms": [s["label"] if isinstance(s, dict) else s
                            for s in _clean(d["synonyms"])],
               "parentMolecule": {"id": d["parentId"], "name": dname.get(d["parentId"])}
               if d["parentId"] else None,
               "childMolecules": [{"id": c, "name": dname.get(c, c)}
                                  for c in _clean(d["childChemblIds"])],
               "mechanisms": moa.get(d["id"], []), "drugWarnings": warn.get(d["id"], []),
               "adverseEvents": ae.get(d["id"], [])}
        rows.append((d["id"], d["name"], json.dumps(doc)))
        aliases.append(((d["name"] or d["id"]).upper(), "drug", d["id"]))
        for x in _clean(d["crossReferences"]):
            if x["source"] == "drugbank":
                aliases += [(i.upper(), "drug", d["id"]) for i in x["ids"] or []]
    con.executemany("INSERT INTO drug VALUES (?,?,?)", rows)
    con.executemany("INSERT INTO alias VALUES (?,?,?)", aliases)
    print(f"  {len(rows)} drugs", flush=True)

    # -- associations and candidates ---------------------------------------------------------
    for f in _files("association_overall_direct"):
        t = pq.read_table(f, columns=["diseaseId", "targetId", "associationScore"])
        con.executemany("INSERT INTO assoc VALUES (?,?,?)",
                        zip(*(t.column(i).to_pylist() for i in range(3))))
    con.executemany("INSERT INTO candidate VALUES (?,?,?,?)",
                    [(r["drugId"], r["diseaseId"], None, r["maxClinicalStage"])
                     for r in _table("clinical_indication", ["drugId", "diseaseId",
                                                             "maxClinicalStage"])])
    con.executemany("INSERT INTO candidate VALUES (?,?,?,?)",
                    [(r["drugId"], None, r["targetId"], r["maxClinicalStage"])
                     for r in _table("clinical_target", ["drugId", "targetId",
                                                         "maxClinicalStage"])])
    print("  associations done", flush=True)

    # -- Europe PMC literature: top papers per (disease, target) ----------------------------
    con.execute("CREATE TEMP TABLE lit_raw(disease TEXT, target TEXT, score REAL, pmid TEXT)")
    for i, f in enumerate(_files("evidence_europepmc")):
        df = pq.read_table(f, columns=["diseaseId", "targetId", "score",
                                       "literature"]).to_pandas()
        df = df[df["literature"].map(lambda x: x is not None and len(x) > 0)]
        df = df.explode("literature").sort_values(["diseaseId", "targetId", "score"],
                                                  ascending=[True, True, False])
        df = df.drop_duplicates(["diseaseId", "targetId", "literature"])
        df = df.groupby(["diseaseId", "targetId"]).head(LIT_PER_PAIR)
        con.executemany("INSERT INTO lit_raw VALUES (?,?,?,?)",
                        df[["diseaseId", "targetId", "score", "literature"]]
                        .itertuples(index=False, name=None))
        if i % 20 == 0:
            print(f"  ... europepmc {i + 1}/{len(_files('evidence_europepmc'))}",
                  file=sys.stderr, flush=True)
    con.execute(f"""INSERT INTO lit SELECT disease, target, score, pmid FROM (
        SELECT *, row_number() OVER (PARTITION BY disease, target ORDER BY score DESC) AS rn
        FROM (SELECT disease, target, max(score) AS score, pmid FROM lit_raw
              GROUP BY disease, target, pmid)) WHERE rn <= {LIT_PER_PAIR}""")
    con.execute("DROP TABLE lit_raw")
    print("  literature done", flush=True)

    con.executescript("""
    CREATE INDEX assoc_d ON assoc(disease, score);
    CREATE INDEX assoc_t ON assoc(target, score);
    CREATE INDEX cand_drug ON candidate(drug);
    CREATE INDEX cand_disease ON candidate(disease);
    CREATE INDEX cand_target ON candidate(target);
    CREATE INDEX lit_dt ON lit(disease, target);
    CREATE INDEX alias_term ON alias(term);
    CREATE INDEX obsolete_old ON obsolete(old);
    CREATE VIRTUAL TABLE name_fts USING fts5(name, entity UNINDEXED, id UNINDEXED);
    INSERT INTO name_fts SELECT symbol || ' ' || coalesce(name, ''), 'target', id FROM target;
    INSERT INTO name_fts SELECT name, 'disease', id FROM disease;
    INSERT INTO name_fts SELECT coalesce(name, id), 'drug', id FROM drug;
    """)
    finish("opentargets", con)
