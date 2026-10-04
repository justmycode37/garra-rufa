"""NCBI E-utilities for db=clinvar (esearch / esummary / elink clinvar->pubmed) from
clinvar.sqlite. Search terms understood: <id>[GID] / [HGNC] / [gene] / [TRID] / [VRID],
optionally "AND" the pathogenic / likely pathogenic clinsig filter."""

from __future__ import annotations

import json
import re

from garra.local import connect
from garra.local.router import Reply, Req, register

HOST = "eutils.ncbi.nlm.nih.gov"
_FIELD = re.compile(r"^\s*\(?\s*\"?([^\"\[\]]+?)\"?\s*\[(\w+)\]\s*\)?\s*(?:AND\s+(.*))?$", re.S)


def db():
    return connect("clinvar")


def _trait_key(local: str) -> tuple[str, str]:
    s = local.strip().upper()
    for pre, key in (("MONDO_", "MONDO"), ("MONDO:", "MONDO"), ("ORPHA", "ORPHA"),
                     ("HP_", "HP"), ("HP:", "HP")):
        if s.startswith(pre):
            return key, s[len(pre):].lstrip(":_")
    if re.fullmatch(r"C\d+|CN\d+", s):
        return "UMLS", s
    return "OMIM", s


def search(term: str, n: int) -> list[str] | None:
    m = _FIELD.match(term)
    if not m:
        return None
    value, field, rest = m[1].strip(), m[2].upper(), (m[3] or "").lower()
    if rest and "pathogenic" not in rest:
        return None
    patho = " AND v.pathogenic=1" if rest else ""
    con = db()
    if field == "GID":
        sql, args = "SELECT v.vid FROM vgene g JOIN variant v ON v.vid=g.vid WHERE g.geneid=?", \
            (value,)
    elif field == "HGNC":
        hg = value if value.upper().startswith("HGNC:") else f"HGNC:{value}"
        sql, args = "SELECT v.vid FROM vgene g JOIN variant v ON v.vid=g.vid WHERE g.hgnc=?", \
            (hg,)
    elif field in ("GENE", "GENE_NAME", "SYM"):
        sql, args = ("SELECT v.vid FROM vgene g JOIN variant v ON v.vid=g.vid "
                     "WHERE g.symbol=? COLLATE NOCASE", (value,))
    elif field == "TRID":
        p, loc = _trait_key(value)
        sql, args = ("SELECT v.vid FROM vtrait t JOIN variant v ON v.vid=t.vid "
                     "WHERE t.prefix=? AND t.local=?", (p, loc))
    elif field == "VRID":
        sql, args = "SELECT v.vid FROM variant v WHERE v.rs=?", (value.lower().lstrip("rs"),)
    else:
        return None
    rows = con.execute(f"{sql}{patho} GROUP BY v.vid ORDER BY v.rank DESC, v.nsub DESC, "
                       f"v.vid DESC LIMIT ?", (*args, n)).fetchall()
    return [str(r[0]) for r in rows]


def summary(vid: str) -> dict | None:
    con = db()
    row = con.execute("SELECT vid, name, rs, germline, review, oncogenicity, clinical_impact, "
                      "traits, clingen FROM variant WHERE vid=?", (int(vid),)).fetchone()
    if not row:
        return None
    vid_, name, rs, germ, review, onco, impact, traits, clingen = row
    genes = [{"symbol": s, "geneid": g} for g, s in con.execute(
        "SELECT geneid, symbol FROM vgene WHERE vid=?", (vid_,)) if s or g]
    xrefs = ([{"db_source": "dbSNP", "db_id": rs}] if rs else []) + \
        ([{"db_source": "ClinGen", "db_id": clingen}] if clingen else [])
    tr = json.loads(traits or "[]")
    return {
        "uid": str(vid_), "obj_type": "", "accession": f"VCV{vid_:09d}", "title": name,
        "genes": genes, "variation_set": [{"variation_name": name, "variation_xrefs": xrefs}],
        "germline_classification": {"description": germ, "review_status": review,
                                    "trait_set": tr},
        "oncogenicity_classification": {"description": onco or "", "trait_set": []},
        "clinical_impact_classification": {"description": impact or "", "trait_set": []},
    }


@register(HOST, "/entrez/eutils/", "clinvar")
def eutils(req: Req) -> Reply | None:
    if (req.get("dbfrom") or req.get("db") or "").lower() != "clinvar":
        return None
    util = req.path.rsplit("/", 1)[-1].removesuffix(".fcgi")
    if util == "esearch":
        n = int(req.get("retmax") or 20)
        ids = search(req.get("term") or "", n)
        if ids is None:
            return None
        return Reply({"header": {"type": "esearch"}, "esearchresult": {
            "count": str(len(ids)), "retmax": str(len(ids)), "retstart": "0", "idlist": ids}})
    if util == "esummary":
        ids = req.getall("id")
        res: dict = {"uids": []}
        for i in ids:
            s = summary(i)
            if s:
                res["uids"].append(s["uid"])
                res[s["uid"]] = s
        return Reply({"header": {"type": "esummary"}, "result": res})
    if util == "elink" and (req.get("db") or "").lower() == "pubmed":
        sets = []
        for i in req.getall("id"):
            pm = [r[0] for r in db().execute("SELECT pmid FROM cite WHERE vid=? ORDER BY "
                                             "CAST(pmid AS INTEGER) DESC", (int(i),))]
            sets.append({"dbfrom": "clinvar", "ids": [i], "linksetdbs": [
                {"dbto": "pubmed", "linkname": "clinvar_pubmed", "links": pm}] if pm else []})
        return Reply({"header": {"type": "elink"}, "linksets": sets})
    return None
