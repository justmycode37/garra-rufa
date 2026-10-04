"""clinicaltrials.sqlite from the full ClinicalTrials.gov JSON export (ctg-studies.json.zip),
replacing GET /api/v2/studies for condition / MeSH / free-text searches.

Each study keeps only the modules the project reads (identification, status, sponsors,
officials, conditions, design phase/type, brief summary, references, condition MeSH),
zlib-compressed.

  study(nct, updated, status, doc)        doc = compressed JSON in API shape
  cmesh(mesh, nct)                        derivedSection condition MeSH ids
  study_fts(cond, title, summary, nct)    FTS5: conditions + keywords + MeSH terms / title
"""

from __future__ import annotations

import json
import sys
import zipfile
import zlib

from garra.local import RAW, finish, writer

ZIP = RAW / "clinicaltrials" / "ctg-studies.json.zip"


def ready() -> bool:
    return ZIP.is_file()


def _slim(d: dict) -> dict:
    p = d.get("protocolSection") or {}
    keep = {k: p[k] for k in ("identificationModule", "statusModule",
                              "sponsorCollaboratorsModule", "conditionsModule") if k in p}
    if "descriptionModule" in p:
        keep["descriptionModule"] = {"briefSummary": p["descriptionModule"].get("briefSummary")}
    if "designModule" in p:
        keep["designModule"] = {k: p["designModule"].get(k) for k in ("studyType", "phases")}
    off = (p.get("contactsLocationsModule") or {}).get("overallOfficials")
    if off:
        keep["contactsLocationsModule"] = {"overallOfficials": off}
    refs = (p.get("referencesModule") or {}).get("references")
    if refs:
        keep["referencesModule"] = {"references": refs}
    cb = (d.get("derivedSection") or {}).get("conditionBrowseModule") or {}
    out = {"protocolSection": keep, "hasResults": d.get("hasResults")}
    if cb.get("meshes"):
        out["derivedSection"] = {"conditionBrowseModule": {"meshes": cb["meshes"]}}
    return out


def build() -> None:
    con = writer("clinicaltrials")
    con.executescript("""
    CREATE TABLE study(nct TEXT PRIMARY KEY, updated TEXT, status TEXT, doc BLOB);
    CREATE TABLE cmesh(mesh TEXT, nct TEXT);
    CREATE VIRTUAL TABLE study_fts USING fts5(cond, title, summary, nct UNINDEXED);
    """)
    z = zipfile.ZipFile(ZIP)
    names = [n for n in z.namelist() if n.endswith(".json")]
    studies, mesh, fts = [], [], []

    def flush():
        con.executemany("INSERT OR REPLACE INTO study VALUES (?,?,?,?)", studies)
        con.executemany("INSERT INTO cmesh VALUES (?,?)", mesh)
        con.executemany("INSERT INTO study_fts VALUES (?,?,?,?)", fts)
        studies.clear(), mesh.clear(), fts.clear()

    for i, n in enumerate(names):
        d = _slim(json.loads(z.read(n)))
        p = d["protocolSection"]
        nct = (p.get("identificationModule") or {}).get("nctId")
        if not nct:
            continue
        st = p.get("statusModule") or {}
        upd = (st.get("lastUpdatePostDateStruct") or {}).get("date") or ""
        cm = p.get("conditionsModule") or {}
        meshes = ((d.get("derivedSection") or {}).get("conditionBrowseModule") or {}) \
            .get("meshes") or []
        studies.append((nct, upd, st.get("overallStatus"),
                        zlib.compress(json.dumps(d, separators=(",", ":")).encode(), 6)))
        mesh += [(m["id"], nct) for m in meshes if m.get("id")]
        ident = p.get("identificationModule") or {}
        fts.append((" | ".join([*(cm.get("conditions") or []), *(cm.get("keywords") or []),
                                *(m.get("term", "") for m in meshes)]),
                    " | ".join(x for x in (ident.get("briefTitle"), ident.get("officialTitle"))
                               if x),
                    (p.get("descriptionModule") or {}).get("briefSummary") or "", nct))
        if len(studies) >= 20000:
            flush()
            print(f"  ... {i + 1}/{len(names)} studies", file=sys.stderr, flush=True)
    flush()
    print(f"  {len(names)} studies", flush=True)
    con.executescript("""
    CREATE INDEX cmesh_m ON cmesh(mesh);
    CREATE INDEX study_updated ON study(updated);
    INSERT INTO study_fts(study_fts) VALUES ('optimize');
    """)
    finish("clinicaltrials", con)
