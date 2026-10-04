"""pubtator.sqlite from the PubTator 3 bulk files, replacing the PubTator 3 API (entity
autocomplete, concept search, relations, BioC export) and LitVar 2 (variant papers).

Disease / gene / chemical annotations and relations are kept for the papers in
pubmed.sqlite (build that first); variant mentions are kept for all of PubMed.

  concept(cid, pid, type, db_id, name, n)   pid = PubTator id (@DISEASE_Marfan_Syndrome)
  ann(cid, pmid)                            concept mentioned in paper
  rel(pmid, type, a, b)                     PubTator relations (concept ids)
  relcount(a, b, type, n)                   papers per relation
  mut(pmid, rs, hgvs, gene)                 variant mentions (rsID and/or HGVS + gene)
  rscount(rs, n)                            papers per rsID
"""

from __future__ import annotations

import gzip
import re
import sqlite3

from garra.local import RAW, db_path, finish, writer

DIR = RAW / "pubtator"
FILES = {"Disease": "disease2pubtator3.gz", "Gene": "gene2pubtator3.gz",
         "Chemical": "chemical2pubtator3.gz"}
_RS = re.compile(r"(?:^|;)RS#:(\d+)")
_HGVS = re.compile(r"(?:^|;)HGVS:([^;]+)")
_GENE = re.compile(r"(?:^|;)CorrespondingGene:(\d+)")


def ready() -> bool:
    return all((DIR / f).is_file() for f in (*FILES.values(), "mutation2pubtator3.gz",
                                             "relation2pubtator3.gz")) \
        and db_path("pubmed").is_file()


def _pid(type_: str, name: str) -> str:
    return f"@{type_.upper()}_" + re.sub(r"\s+", "_", name.strip())


def _lines(name):
    with gzip.open(DIR / name, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3:
                yield p


def build() -> None:
    pm = sqlite3.connect(db_path("pubmed"))
    subset = {r[0] for r in pm.execute("SELECT pmid FROM paper")}
    pm.close()
    print(f"  {len(subset)} papers in the PubMed subset", flush=True)
    mesh_name: dict[str, str] = {}
    if db_path("mesh").is_file():
        m = sqlite3.connect(db_path("mesh"))
        mesh_name = dict(m.execute("SELECT ui, name FROM rec"))
        m.close()
    symbol: dict[str, str] = {}
    if db_path("ontology").is_file():
        o = sqlite3.connect(db_path("ontology"))
        symbol = {e: s for e, s in o.execute("SELECT entrez, symbol FROM hgnc "
                                             "WHERE entrez IS NOT NULL")}
        o.close()

    con = writer("pubtator")
    con.executescript("""
    CREATE TABLE concept(cid INTEGER PRIMARY KEY, pid TEXT, type TEXT, db_id TEXT,
                         name TEXT, n INTEGER DEFAULT 0);
    CREATE TABLE ann(cid INTEGER, pmid INTEGER, PRIMARY KEY (cid, pmid)) WITHOUT ROWID;
    CREATE TABLE rel(pmid INTEGER, type TEXT, a INTEGER, b INTEGER);
    CREATE TABLE mut(pmid INTEGER, rs TEXT, hgvs TEXT, gene TEXT, mention TEXT);
    """)
    cids: dict[tuple[str, str], int] = {}
    names: dict[int, str] = {}

    def cid(type_: str, db_id: str, mention: str | None = None) -> int:
        k = (type_, db_id)
        c = cids.get(k)
        if c is None:
            c = cids[k] = len(cids) + 1
            if type_ == "Gene":
                nm = symbol.get(db_id) or (mention or db_id).split("|")[0]
            elif db_id.startswith("MESH:"):
                nm = mesh_name.get(db_id[5:]) or (mention or db_id).split("|")[0]
            else:
                nm = (mention or db_id).split("|")[0]
            names[c] = nm
        return c

    for type_, fn in FILES.items():
        batch, kept, seen = [], 0, 0
        for p in _lines(fn):
            seen += 1
            try:
                pmid = int(p[0])
            except ValueError:
                continue
            if pmid not in subset or p[2] in ("-", ""):
                continue
            for db_id in p[2].split(";"):
                if db_id and db_id != "-":
                    batch.append((cid(type_, db_id, p[3] if len(p) > 3 else None), pmid))
            if len(batch) >= 500_000:
                con.executemany("INSERT OR IGNORE INTO ann VALUES (?,?)", batch)
                kept += len(batch)
                batch.clear()
        con.executemany("INSERT OR IGNORE INTO ann VALUES (?,?)", batch)
        kept += len(batch)
        print(f"  {type_}: {kept} annotations kept of {seen} rows", flush=True)

    rels = []
    for p in _lines("relation2pubtator3.gz"):
        if len(p) < 4:
            continue
        try:
            pmid = int(p[0])
        except ValueError:
            continue
        if pmid not in subset:
            continue
        ends = []
        for e in (p[2], p[3]):
            t, _, db_id = e.partition("|")
            ends.append(cid(t, db_id) if t and db_id else None)
        if all(ends):
            rels.append((pmid, p[1], *ends))
    con.executemany("INSERT INTO rel VALUES (?,?,?,?)", rels)
    print(f"  {len(rels)} relations", flush=True)

    muts = []
    for p in _lines("mutation2pubtator3.gz"):
        try:
            pmid = int(p[0])
        except ValueError:
            continue
        concept, mention = p[2], (p[3] if len(p) > 3 else "")
        if concept.startswith("rs"):
            muts.append((pmid, concept, None, None, mention))
        else:
            rs = _RS.search(concept)
            hg = _HGVS.search(concept)
            gene = _GENE.search(concept)
            muts.append((pmid, f"rs{rs[1]}" if rs else None, hg[1] if hg else None,
                         gene[1] if gene else None, mention))
        if len(muts) >= 500_000:
            con.executemany("INSERT INTO mut VALUES (?,?,?,?,?)", muts)
            muts.clear()
    con.executemany("INSERT INTO mut VALUES (?,?,?,?,?)", muts)

    con.executemany("INSERT INTO concept(cid, pid, type, db_id, name) VALUES (?,?,?,?,?)",
                    [(c, _pid(t, names[c]), t, d, names[c]) for (t, d), c in cids.items()])
    print(f"  {len(cids)} concepts; indexing ...", flush=True)
    con.executescript("""
    CREATE INDEX ann_pmid ON ann(pmid);
    UPDATE concept SET n = (SELECT count(*) FROM ann WHERE ann.cid = concept.cid);
    CREATE INDEX concept_pid ON concept(pid);
    CREATE INDEX concept_db ON concept(type, db_id);
    CREATE INDEX rel_pmid ON rel(pmid);
    CREATE TABLE relcount AS SELECT a, b, type, count(DISTINCT pmid) AS n FROM rel
        GROUP BY a, b, type;
    CREATE INDEX relcount_a ON relcount(a);
    CREATE INDEX relcount_b ON relcount(b);
    CREATE INDEX mut_rs ON mut(rs);
    CREATE INDEX mut_gene ON mut(gene, hgvs);
    """)
    add_rscount(con)
    finish("pubtator", con)


def add_rscount(con: sqlite3.Connection) -> None:
    """rscount(rs, n): papers per rsID, for LitVar's gene variant list."""
    con.executescript("""
    DROP TABLE IF EXISTS rscount;
    CREATE TABLE rscount(rs TEXT PRIMARY KEY, n INTEGER) WITHOUT ROWID;
    INSERT INTO rscount SELECT rs, count(DISTINCT pmid) FROM mut WHERE rs IS NOT NULL
        GROUP BY rs;
    """)
    con.commit()
