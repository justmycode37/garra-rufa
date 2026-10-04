"""LitVar 2 API (www.ncbi.nlm.nih.gov/research/litvar2-api) from the PubTator 3 variant
mentions (pubtator.sqlite, all of PubMed) and ClinVar (clinvar.sqlite) for gene and
protein-change lookups.

  /variant/autocomplete/?query=<gene> <p.change> | rsID
  /variant/get/<litvar id>/publications           litvar@rs<n>## or litvar@#<gene id>#<hgvs>
  /variant/search/gene/<symbol>                   rsIDs of the gene with paper counts
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from garra.local import available, connect
from garra.local.router import Reply, Req, register

HOST = "www.ncbi.nlm.nih.gov"
BASE = "/research/litvar2-api"
AA = dict(zip("ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR "
              "VAL TER".split(), "ARNDCQEGHILKMFPSTWYV*"))
AA3 = {v: k.capitalize() for k, v in AA.items()}


def db():
    return connect("pubtator")


def one_letter(hgvs: str) -> str:
    """p.Cys1039Tyr -> p.C1039Y (PubTator's normalized spelling)."""
    m = re.fullmatch(r"p\.\(?([A-Za-z]{3}|[A-Z*])(\d+)([A-Za-z]{3}|[A-Z*=])\)?", hgvs.strip())
    if not m:
        return hgvs.strip()
    ref, pos, alt = m.groups()
    return f"p.{AA.get(ref.upper(), ref)}{pos}{AA.get(alt.upper(), alt)}"


def _entrez(symbol: str) -> str | None:
    if not available("ontology"):
        return None
    from garra.local import ontology
    g = ontology.hgnc("symbol", symbol)
    return g[0]["entrez_id"] if g and g[0].get("entrez_id") else None


def _pmids_rs(rs: str) -> list[int]:
    return [r[0] for r in db().execute("SELECT DISTINCT pmid FROM mut WHERE rs=? "
                                       "ORDER BY pmid DESC", (rs.lower(),))]


def _pmids_hgvs(gene: str, hgvs: str) -> list[int]:
    return [r[0] for r in db().execute("SELECT DISTINCT pmid FROM mut WHERE gene=? AND hgvs=? "
                                       "ORDER BY pmid DESC", (gene, hgvs))]


def _clinvar_rs(symbol: str, hgvs3: str) -> list[str]:
    if not available("clinvar"):
        return []
    rows = connect("clinvar").execute(
        "SELECT DISTINCT v.rs FROM vgene g JOIN variant v ON v.vid=g.vid WHERE "
        "g.symbol=? COLLATE NOCASE AND v.rs IS NOT NULL AND v.name LIKE ?",
        (symbol, f"%({hgvs3})%")).fetchall()
    return [f"rs{r[0]}" for r in rows]


@register(HOST, BASE + "/", "pubtator")
def litvar(req: Req) -> Reply | None:
    path = unquote(req.path[len(BASE):])
    if path.rstrip("/") == "/variant/autocomplete":
        q = (req.get("query") or "").strip()
        if re.fullmatch(r"rs\d+", q, re.I):
            n = len(_pmids_rs(q))
            return Reply([{"_id": f"litvar@{q.lower()}##", "rsid": q.lower(), "name": q.lower(),
                           "pmids_count": n}] if n else [])
        m = re.fullmatch(r"([A-Za-z0-9-]+)\s+(p\.\S+)", q)
        if not m:
            return None
        symbol, hgvs = m[1], m[2]
        short = one_letter(hgvs)
        long3 = re.sub(r"^p\.([A-Z*])(\d+)([A-Z*=])$",
                       lambda x: f"p.{AA3.get(x[1], x[1])}{x[2]}{AA3.get(x[3], x[3])}", short)
        out = [{"_id": f"litvar@{rs}##", "rsid": rs, "gene": [symbol], "hgvs": long3,
                "name": long3} for rs in _clinvar_rs(symbol, long3)]
        gid = _entrez(symbol)
        if not out and gid and _pmids_hgvs(gid, short):
            out.append({"_id": f"litvar@#{gid}#{short}", "gene": [symbol], "hgvs": long3,
                        "name": long3})
        return Reply(out)
    m = re.fullmatch(r"/variant/get/(.+)/publications", path)
    if m:
        lid = m[1]
        rs = re.fullmatch(r"litvar@(rs\d+)##", lid)
        if rs:
            pm = _pmids_rs(rs[1])
        else:
            g = re.fullmatch(r"litvar@#(\d+)#(.+)", lid)
            if not g:
                return None
            pm = _pmids_hgvs(g[1], g[2])
        if not pm:
            return Reply({"detail": "no publications"}, 400)
        return Reply({"pmids": pm, "pmcids": [], "pmids_count": len(pm)})
    m = re.fullmatch(r"/variant/search/gene/([A-Za-z0-9-]+)", path)
    if m:
        symbol = m[1]
        gid = _entrez(symbol)
        rs_ids: set[str] = set()
        if available("clinvar"):
            rs_ids |= {f"rs{r[0]}" for r in connect("clinvar").execute(
                "SELECT DISTINCT v.rs FROM vgene g JOIN variant v ON v.vid=g.vid WHERE "
                "g.symbol=? COLLATE NOCASE AND v.rs IS NOT NULL", (symbol,))}
        if gid:
            rs_ids |= {r[0] for r in db().execute(
                "SELECT DISTINCT rs FROM mut WHERE gene=? AND rs IS NOT NULL", (gid,))}
        lines = []
        ids = sorted(rs_ids)
        counts: dict[str, int] = {}
        if db().execute("SELECT 1 FROM sqlite_master WHERE name='rscount'").fetchone():
            for i in range(0, len(ids), 900):
                chunk = ids[i:i + 900]
                counts.update(db().execute(
                    f"SELECT rs, n FROM rscount WHERE rs IN ({','.join('?' * len(chunk))})",
                    chunk))
        else:  # index built before rscount existed
            for rs in ids:
                counts[rs] = db().execute("SELECT count(DISTINCT pmid) FROM mut WHERE rs=?",
                                          (rs,)).fetchone()[0]
        for rs in ids:
            n = counts.get(rs)
            if n:
                lines.append(f"{{'_id': 'litvar@{rs}##', 'pmids_count': {n}, 'rsid': '{rs}'}}")
        return Reply("\n".join(lines), content_type="text/plain")
    return None
