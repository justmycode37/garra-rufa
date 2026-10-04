"""pubmed.sqlite: the rare-disease subset of the PubMed baseline + update files that are
already downloaded (data/pubmed-nih-gov/baseline, data/pubmed/updatefiles), replacing
E-utilities esearch / efetch / esummary for db=pubmed.

All of PubMed (~40M records) with a phrase-capable full-text index would need ~35-45 GB
and hours to index, so only papers about rare diseases are indexed. A paper is kept when
  - PubTator tags it with a disease concept that Orphanet / Mondo mark as rare
    (MeSH ids with an exact or narrower Orphanet mapping, OMIM ids of rare diseases), or
  - one of its MeSH headings / supplementary concepts is such a MeSH id, or
  - its title or abstract contains a rare-disease name (Orphanet preferred terms and
    synonyms, Mondo "rare" labels and exact synonyms; abbreviations excluded).
Every literature query plan.py builds is anchored on a disease, so the subset answers
them; papers outside it (e.g. PMIDs cited by graph sources) fall back to the API.

  paper(pmid, year, journal, doc)       doc = zlib JSON (title, abstract sections, authors,
                                        MeSH with major / qualifiers, types, keywords, ids)
  paper_fts(tiab, mesh, majr, pt, supp) contentless FTS5, rowid = PMID
  meta(key, value)
"""

from __future__ import annotations

import array
import bisect
import gzip
import json
import multiprocessing as mp
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
import zlib

from garra.local import RAW, db_path, finish, writer
from garra.paths import DATA_DIR

BASELINE = DATA_DIR / "pubmed-nih-gov" / "baseline"
UPDATES = [DATA_DIR / "pubmed" / "updatefiles", DATA_DIR / "pubmed-nih-gov" / "updatefiles"]
DISEASE2PUBTATOR = RAW / "pubtator" / "disease2pubtator3.gz"
SEED = DATA_DIR / "local" / "pubmed_seed.bin"
SEP = " xxsepxx "  # between MeSH entries, so a phrase cannot span two headings
_WORD = re.compile(r"[a-z0-9]+")
_ABBR = re.compile(r"^[A-Z0-9-]{2,8}$")
COMMON = {"syndrome", "disease", "disorder", "anemia", "dwarfism", "deafness", "obesity",
          "epilepsy", "diabetes", "asthma", "cancer", "tumor", "tumour", "carcinoma",
          "lymphoma", "leukemia", "sarcoma", "infection", "fever", "pneumonia", "migraine",
          "glaucoma", "cataract", "hypertension", "arthritis", "dementia", "autism",
          "schizophrenia", "depression", "psoriasis", "eczema", "hepatitis", "nephritis",
          "cardiomyopathy", "neuropathy", "myopathy", "hypothyroidism", "hyperthyroidism",
          "infertility", "scoliosis", "polydactyly", "syndactyly", "microcephaly",
          "macrocephaly", "hydrocephalus", "blindness", "ataxia", "dystonia", "tremor"}


# conditions Orphanet / Mondo list (often as rare subtypes or rare cancers) that are common
# in the literature at large; matching them would pull in a large share of PubMed
NOT_RARE = {"tuberculosis", "metabolic syndrome", "autism spectrum disorder", "preeclampsia",
            "acute lung injury", "renal fibrosis", "neutropenia", "encephalitis", "vasculitis",
            "spinal cord injury", "spinal cord injuries", "age related macular degeneration",
            "macular degeneration", "gastric cancer", "stomach neoplasms",
            "hepatocellular carcinoma", "carcinoma hepatocellular", "pancreatic cancer",
            "pancreatic neoplasms", "liver cancer", "liver neoplasms", "thyroid cancer",
            "thyroid carcinoma", "thyroid neoplasms", "esophageal cancer",
            "esophageal neoplasms", "lung adenocarcinoma", "adenocarcinoma of lung",
            "renal cell carcinoma", "carcinoma renal cell", "clear cell renal cell carcinoma",
            "pancreatic ductal adenocarcinoma", "pancreatic adenocarcinoma",
            "esophageal squamous cell carcinoma", "epithelial ovarian cancer",
            "early gastric cancer", "primary liver cancer", "urothelial carcinoma",
            "small cell lung cancer", "small cell lung carcinoma", "pulmonary fibrosis",
            "interstitial lung disease", "lung diseases interstitial", "acute liver failure",
            "liver failure acute", "acute leukemia", "chronic kidney disease",
            "colorectal cancer", "breast cancer", "prostate cancer", "lung cancer"}


def ready() -> bool:
    return any(BASELINE.glob("*.xml.gz")) and DISEASE2PUBTATOR.is_file() and all(
        db_path(n).is_file() for n in ("ontology", "orphadata", "mesh"))


def norm_words(s: str) -> str:
    return " ".join(_WORD.findall(s.lower()))


# -- rare-disease vocabulary --------------------------------------------------------------
def vocabulary() -> tuple[set[str], set[str], set[str]]:
    """(MeSH ids, PubTator disease concept ids, normalized names) of rare diseases."""
    mesh_ids: set[str] = set()
    omim: set[str] = set()
    names: set[str] = set()
    orpha = sqlite3.connect(db_path("orphadata"))
    # only disorders and subtypes: groups map onto generic headings ("Rare sleep disorder"
    # is an exact mapping of "Sleep Wake Disorders")
    for src, ref, rel in orpha.execute(
            "SELECT x.source, x.reference, x.relation FROM oxref x JOIN disorder d "
            "ON d.code=x.code WHERE d.grp != 'Group of disorders'"):
        exactish = (rel or "").startswith(("E ", "BTNT", "E("))
        if not exactish:
            continue
        if src == "MeSH":
            mesh_ids.add(ref)
        elif src == "OMIM":
            omim.add(ref)
    for (name,) in orpha.execute("SELECT o.name FROM oname o JOIN disorder d ON d.code=o.code "
                                 "WHERE d.grp != 'Group of disorders' OR o.is_pref=1"):
        names.add(name)
    orpha.close()
    ont = sqlite3.connect(db_path("ontology"))
    rare = {c for (c,) in ont.execute("SELECT curie FROM subset WHERE subset='rare'")}
    for c, x in ont.execute("SELECT curie, xref FROM xref WHERE xref LIKE 'OMIM:%'"):
        if c in rare:
            omim.add(x.split(":", 1)[1])
    for c, name, scope, typ in ont.execute("SELECT curie, name, scope, type FROM name "
                                           "WHERE ont='mondo'"):
        if c in rare and scope in ("LABEL", "EXACT") and (typ or "").lower() not in (
                "abbreviation", "acronym"):
            names.add(name)
    ont.close()
    keep = set()
    for n in names:
        if _ABBR.match(n.strip()):
            continue
        k = norm_words(n)
        ws = k.split()
        if len(k) < 6 or not ws or ws[0] == "obsolete":
            continue
        if len(ws) == 1 and (len(k) < 8 or k in COMMON):
            continue
        if len(ws) == 2 and ws[1] in ("syndrome", "disease") and len(ws[0]) <= 3:
            continue  # "xy syndrome": too ambiguous as free text
        keep.add(k)
    keep -= NOT_RARE
    if db_path("mesh").is_file():
        m = sqlite3.connect(db_path("mesh"))
        common = {ui for ui, nm in m.execute("SELECT ui, name FROM mterm")
                  if norm_words(nm) in NOT_RARE}
        m.close()
        mesh_ids -= common
    concepts = {f"MESH:{m}" for m in mesh_ids} | {f"OMIM:{o}" for o in omim}
    return mesh_ids, concepts, keep


def build_seed(concepts: set[str]) -> array.array:
    """Sorted PMIDs that PubTator tags with a rare-disease concept."""
    pm = set()
    with gzip.open(DISEASE2PUBTATOR, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.split("\t", 3)
            if len(p) >= 3 and p[2] in concepts:
                try:
                    pm.add(int(p[0]))
                except ValueError:
                    pass
    out = array.array("I", sorted(pm))
    SEED.parent.mkdir(parents=True, exist_ok=True)
    with open(SEED, "wb") as f:
        out.tofile(f)
    return out


# -- worker ------------------------------------------------------------------------------------
_W: dict = {}


def _init(mesh_ids, names, seed_path):
    seed = array.array("I")
    with open(seed_path, "rb") as f:
        seed.frombytes(f.read())
    first: dict[str, set[int]] = {}
    for n in names:
        ws = n.split()
        first.setdefault(ws[0], set()).add(len(ws))
    _W.update(mesh=mesh_ids, names=names, first=first, seed=seed)


def _in_seed(pmid: int) -> bool:
    s = _W["seed"]
    i = bisect.bisect_left(s, pmid)
    return i < len(s) and s[i] == pmid


def _mentions_name(text: str) -> bool:
    ws = _WORD.findall(text.lower())
    names, first = _W["names"], _W["first"]
    for i, w in enumerate(ws):
        lens = first.get(w)
        if lens:
            for n in lens:
                if " ".join(ws[i:i + n]) in names:
                    return True
    return False


def _t(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _parse(path: str) -> tuple[str, list, list[int], int]:
    out, deleted, seen = [], [], 0
    with gzip.open(path) as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag == "DeleteCitation":
                deleted += [int(p.text) for p in el.findall("PMID") if p.text]
                el.clear()
                continue
            if el.tag != "PubmedArticle":
                continue
            seen += 1
            mc = el.find("MedlineCitation")
            pmid = int(mc.findtext("PMID"))
            a = mc.find("Article")
            title = _t(a.find("ArticleTitle"))
            abstract = [[t.get("Label"), _t(t)] for t in a.findall("Abstract/AbstractText")]
            mesh, majr, uis = [], [], []
            for h in mc.findall("MeshHeadingList/MeshHeading"):
                dn = h.find("DescriptorName")
                quals = [q for q in h.findall("QualifierName")]
                major = dn.get("MajorTopicYN") == "Y" or any(
                    q.get("MajorTopicYN") == "Y" for q in quals)
                name = _t(dn)
                uis.append(dn.get("UI"))
                mesh.append(("*" if major else "") + name + "".join(f"/{_t(q)}" for q in quals))
                if major:
                    majr.append(name)
            supp = [(s.get("UI"), _t(s)) for s in mc.findall("SupplMeshList/SupplMeshName")]
            uis += [u for u, _ in supp]
            tiab = title + " " + " ".join(x for _, x in abstract)
            keep = _in_seed(pmid) or any(u in _W["mesh"] for u in uis) or \
                _mentions_name(tiab)
            if not keep:
                el.clear()
                continue
            pd = a.find("Journal/JournalIssue/PubDate")
            year = None
            pubdate = ""
            if pd is not None:
                y = pd.findtext("Year") or (pd.findtext("MedlineDate") or "")[:4]
                year = int(y) if y.isdigit() else None
                pubdate = " ".join(x for x in (pd.findtext("Year"), pd.findtext("Month"),
                                               pd.findtext("Day")) if x) or \
                    (pd.findtext("MedlineDate") or "")
            authors = []
            for au in a.findall("AuthorList/Author"):
                nm = " ".join(x for x in (au.findtext("LastName"), au.findtext("Initials")) if x)
                authors.append(nm or au.findtext("CollectiveName") or "")
            ids = {i.get("IdType"): _t(i)
                   for i in el.findall("PubmedData/ArticleIdList/ArticleId")}
            pts = [_t(x) for x in a.findall("PublicationTypeList/PublicationType")]
            doc = {"t": title, "a": abstract,
                   "j": a.findtext("Journal/ISOAbbreviation"),
                   "jt": a.findtext("Journal/Title"), "y": year, "pd": pubdate,
                   "au": [x for x in authors if x], "m": mesh, "pt": pts,
                   "kw": [_t(k) for k in mc.findall("KeywordList/Keyword") if _t(k)],
                   "s": [n for _, n in supp], "doi": ids.get("doi"), "pmc": ids.get("pmc")}
            mesh_text = SEP.join(m.lstrip("*").replace("/", " ") for m in mesh) + SEP + \
                SEP.join(m.lstrip("*").split("/")[0] for m in mesh)
            out.append((pmid, year, doc["j"] or doc["jt"],
                        zlib.compress(json.dumps(doc, separators=(",", ":")).encode(), 6),
                        tiab, mesh_text, SEP.join(majr), SEP.join(pts),
                        SEP.join(n for _, n in supp)))
            el.clear()
    return path, out, deleted, seen


def build(keep_raw: bool = True) -> None:
    t0 = time.time()
    mesh_ids, concepts, names = vocabulary()
    print(f"  vocabulary: {len(mesh_ids)} MeSH ids, {len(concepts)} PubTator concepts, "
          f"{len(names)} names", flush=True)
    seed = build_seed(concepts)
    print(f"  seed: {len(seed)} PubTator-tagged papers ({(time.time() - t0) / 60:.1f} min)",
          flush=True)
    files = sorted(str(p) for p in BASELINE.glob("*.xml.gz"))
    for d in UPDATES:
        files += sorted(str(p) for p in d.glob("*.xml.gz")) if d.is_dir() else []
    con = writer("pubmed")
    con.executescript("""
    CREATE TABLE paper(pmid INTEGER PRIMARY KEY, year INTEGER, journal TEXT, doc BLOB);
    CREATE VIRTUAL TABLE paper_fts USING fts5(tiab, mesh, majr, pt, supp, content='',
        contentless_delete=1, tokenize='unicode61 remove_diacritics 2');
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    total_seen = kept = 0
    workers = max(1, (mp.cpu_count() or 2) - 2)
    with mp.Pool(workers, initializer=_init, initargs=(mesh_ids, names, str(SEED))) as pool:
        for i, (path, rows, deleted, seen) in enumerate(pool.imap(_parse, files)):
            total_seen += seen
            if deleted:
                q = ",".join("?" * len(deleted))
                con.execute(f"DELETE FROM paper_fts WHERE rowid IN ({q})", deleted)
                con.execute(f"DELETE FROM paper WHERE pmid IN ({q})", deleted)
            pmids = [r[0] for r in rows]
            if pmids:
                for j in range(0, len(pmids), 900):
                    chunk = pmids[j:j + 900]
                    q = ",".join("?" * len(chunk))
                    con.execute(f"DELETE FROM paper_fts WHERE rowid IN ({q})", chunk)
                con.executemany("INSERT OR REPLACE INTO paper VALUES (?,?,?,?)",
                                (r[:4] for r in rows))
                con.executemany("INSERT INTO paper_fts(rowid, tiab, mesh, majr, pt, supp) "
                                "VALUES (?,?,?,?,?,?)", ((r[0], *r[4:]) for r in rows))
            kept += len(rows)
            if i % 25 == 0:
                con.commit()
                el = time.time() - t0
                print(f"  ... {i + 1}/{len(files)} files, {total_seen} records, {kept} kept "
                      f"({el / 60:.1f} min)", file=sys.stderr, flush=True)
    n = con.execute("SELECT count(*) FROM paper").fetchone()[0]
    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("records_seen", str(total_seen)), ("papers", str(n)),
        ("files", json.dumps([p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for p in files])),
        ("built", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))])
    print(f"  {n} papers kept of {total_seen} records; optimizing index ...", flush=True)
    con.execute("INSERT INTO paper_fts(paper_fts) VALUES ('optimize')")
    con.execute("CREATE INDEX paper_year ON paper(year)")
    finish("pubmed", con)
