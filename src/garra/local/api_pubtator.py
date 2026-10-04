"""PubTator 3 API (www.ncbi.nlm.nih.gov/research/pubtator3-api) from pubtator.sqlite and
pubmed.sqlite:

  /entity/autocomplete/?query=&concept=     MeSH entry terms (disease, chemical) or HGNC
                                            symbols / aliases (gene) of concepts in the data
  /search/?text=@A AND @B&page=&size=       papers annotated with every concept; papers
                                            naming the concepts in title/abstract first
  /relations?e1=@A                          relation types with paper counts
  /publications/export/biocjson?pmids=      title + abstract with concept annotations
Only papers in the local PubMed subset carry disease / gene / chemical annotations.
"""

from __future__ import annotations

import functools
import re

from garra.local import available, connect
from garra.local import pubmed as P
from garra.local.router import Reply, Req, register

HOST = "www.ncbi.nlm.nih.gov"
BASE = "/research/pubtator3-api"
TYPES = {"disease": "Disease", "gene": "Gene", "chemical": "Chemical"}
MAX_CANDIDATES = 50_000


def db():
    return connect("pubtator")


def _concepts(pid: str) -> list[tuple[int, str, str]]:
    return db().execute("SELECT cid, name, type FROM concept WHERE pid=?", (pid,)).fetchall()


def autocomplete(query: str, biotype: str, limit: int) -> list[dict]:
    type_ = TYPES.get(biotype)
    if not type_ or not query.strip():
        return []
    con = db()
    rows: list[tuple] = []
    if type_ == "Gene":
        from garra.local import ontology
        for field in ("symbol", "alias_symbol", "prev_symbol"):
            for g in ontology.hgnc(field, query.strip()) if available("ontology") else []:
                if g.get("entrez_id"):
                    rows += con.execute("SELECT pid, db_id, name, n FROM concept WHERE "
                                        "type='Gene' AND db_id=?", (g["entrez_id"],)).fetchall()
            if rows:
                break
    elif available("mesh"):
        from garra.local import mesh
        for ui in mesh.by_name(query):
            rows += con.execute("SELECT pid, db_id, name, n FROM concept WHERE type=? AND "
                                "db_id=?", (type_, f"MESH:{ui}")).fetchall()
    out, seen = [], set()
    for pid, db_id, name, n in sorted(rows, key=lambda r: -(r[3] or 0)):
        if pid in seen:
            continue
        seen.add(pid)
        out.append({"_id": pid, "biotype": biotype, "db_id": db_id.split(":", 1)[-1]
                    if db_id.startswith("MESH:") else db_id,
                    "db": "ncbi_mesh" if db_id.startswith("MESH:") else
                    ("ncbi_gene" if type_ == "Gene" else "omim"), "name": name})
    return out[:limit]


@functools.lru_cache(maxsize=64)  # the provider pages through the same search
def _ranked(pids: tuple[str, ...]) -> list[int] | None:
    sets, names = [], []
    con = db()
    for pid in pids:
        cs = _concepts(pid)
        if not cs:
            return None
        names.append(cs[0][1])
        ph = ",".join("?" * len(cs))
        sets.append(f"SELECT pmid FROM ann WHERE cid IN ({ph})")
    args = [c for pid in pids for c, _, _ in _concepts(pid)]
    cand = [r[0] for r in con.execute(" INTERSECT ".join(sets) + " LIMIT ?",
                                      (*args, MAX_CANDIDATES))]
    if not cand:
        return []
    # papers naming every concept in title / abstract first (best match), then newest
    phrases = []
    for n in names:
        ws = re.findall(r"\w+", n)
        if ws:
            phrases.append('{tiab} : "' + " ".join(ws) + '"')
    first: list[int] = []
    if phrases and available("pubmed"):
        # one MATCH over the whole index, intersected here: FTS5 does not use a
        # "rowid IN (...)" filter to narrow a phrase match, so chunked filtering
        # re-ran the full match per chunk
        q = " AND ".join(phrases)
        want = set(cand)
        first = [r[0] for r in P.db().execute(
            "SELECT rowid FROM paper_fts WHERE paper_fts MATCH ? ORDER BY bm25(paper_fts)",
            (q,)) if r[0] in want]
    seen = set(first)
    return first + sorted((c for c in cand if c not in seen), reverse=True)


def _paper(pmid: int) -> dict:
    d = P.record(pmid) if available("pubmed") else None
    d = d or {}
    return {"_id": str(pmid), "pmid": pmid, "pmcid": d.get("pmc"), "title": d.get("t"),
            "journal": d.get("j") or d.get("jt"), "authors": d.get("au") or [],
            "date": f"{d['y']}-01-01T00:00:00Z" if d.get("y") else None, "doi": d.get("doi")}


@register(HOST, BASE + "/", ("pubtator", "pubmed"))
def pubtator(req: Req) -> Reply | None:
    path = req.path[len(BASE):].rstrip("/")
    if path == "/entity/autocomplete":
        return Reply(autocomplete(req.get("query") or "", (req.get("concept") or "").lower(),
                                  int(req.get("limit") or 10)))
    if path == "/search":
        text = req.get("text") or ""
        pids = [p.strip() for p in text.split(" AND ")]
        if not pids or not all(p.startswith("@") for p in pids):
            return None
        ranked = _ranked(tuple(pids))
        if ranked is None:
            return None
        size = int(req.get("size") or 10)
        page = int(req.get("page") or 1)
        rows = ranked[(page - 1) * size:page * size]
        pages = max(1, -(-len(ranked) // size))
        return Reply({"results": [_paper(p) for p in rows], "count": len(ranked),
                      "total_pages": pages, "current": page, "page_size": size})
    if path == "/relations":
        e1 = req.get("e1") or ""
        cs = _concepts(e1)
        if not cs:
            return None
        ids = [c for c, _, _ in cs]
        ph = ",".join("?" * len(ids))
        rows = db().execute(
            f"SELECT r.type, ca.pid, cb.pid, sum(r.n) AS n FROM relcount r "
            f"JOIN concept ca ON ca.cid=r.a JOIN concept cb ON cb.cid=r.b "
            f"WHERE r.a IN ({ph}) OR r.b IN ({ph}) GROUP BY r.type, ca.pid, cb.pid "
            f"ORDER BY n DESC LIMIT 1000", (*ids, *ids)).fetchall()
        return Reply([{"type": t, "source": a, "target": b, "publications": n}
                      for t, a, b, n in rows])
    if path == "/publications/export/biocjson":
        pmids = [p for p in req.getall("pmids") if p.isdigit()]
        if not pmids or len(P.has(pmids)) != len(set(pmids)):
            return None
        return Reply({"PubTator3": [_bioc(int(p)) for p in pmids]})
    return None


def _bioc(pmid: int) -> dict:
    d = P.record(pmid) or {}
    con = db()
    anns = []
    for cid, pid, type_, db_id, name in con.execute(
            "SELECT c.cid, c.pid, c.type, c.db_id, c.name FROM ann a JOIN concept c "
            "ON c.cid=a.cid WHERE a.pmid=?", (pmid,)):
        anns.append({"id": str(cid), "infons": {
            "identifier": db_id, "type": type_, "accession": pid, "name": name,
            "database": "ncbi_gene" if type_ == "Gene" else "ncbi_mesh",
            "biotype": type_.lower()}, "text": name, "locations": []})
    rels = [{"name": f"{t}|{a}|{b}"} for t, a, b in con.execute(
        "SELECT DISTINCT r.type, ca.pid, cb.pid FROM rel r JOIN concept ca ON ca.cid=r.a "
        "JOIN concept cb ON cb.cid=r.b WHERE r.pmid=?", (pmid,))]
    abstract = " ".join(t for _, t in d.get("a") or [])
    return {"_id": f"{pmid}|None", "id": str(pmid), "pmid": pmid, "pmcid": d.get("pmc"),
            "journal": d.get("j"), "authors": d.get("au") or [], "infons": {},
            "passages": [{"infons": {"type": "title"}, "offset": 0, "text": d.get("t") or "",
                          "annotations": anns},
                         {"infons": {"type": "abstract"}, "offset": len(d.get("t") or "") + 1,
                          "text": abstract, "annotations": []}],
            "relations": [], "relations_display": rels}
