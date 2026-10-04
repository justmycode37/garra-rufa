"""GTEx Portal API v2 (https://gtexportal.org/api/v2) from gtex.sqlite: the endpoints
sources/gtex.py uses (gene reference, median expression, single-tissue e/sQTLs, top
expressed genes, eGenes, tissue list, dbSNP -> variant). QTLs are the strongest
TOP_PER_GENE variants per gene (see build_gtex.py), so a weak QTL may be missing."""

from __future__ import annotations

import json

from garra.local import connect
from garra.local.router import Reply, Req, register

HOST = "gtexportal.org"
_tissues: list[dict] | None = None


def db():
    return connect("gtex")


def tissues() -> list[dict]:
    global _tissues
    if _tissues is None:
        row = db().execute("SELECT value FROM meta WHERE key='tissueSiteDetail'").fetchone()
        _tissues = json.loads(row[0]).get("data", []) if row else []
    return _tissues


def _onto(tissue: str) -> str | None:
    return next((t["ontologyId"] for t in tissues() if t["tissueSiteDetailId"] == tissue), None)


def _data(items: list) -> Reply:
    return Reply({"data": items, "paging_info": {"numberOfPages": 1, "page": 0,
                                                 "maxItemsPerPage": len(items),
                                                 "totalNumberOfItems": len(items)}})


def _gene(gencode: str, symbol: str, entrez: str | None) -> dict:
    return {"gencodeId": gencode, "geneSymbol": symbol, "geneSymbolUpper": symbol.upper(),
            "entrezGeneId": int(entrez) if entrez and entrez.isdigit() else entrez,
            "gencodeVersion": "v39", "genomeBuild": "GRCh38/hg38"}


def _symbols(gencodes) -> dict[str, str]:
    ids = list(dict.fromkeys(gencodes))
    if not ids:
        return {}
    return dict(db().execute(f"SELECT gencode, symbol FROM gene WHERE gencode IN "
                             f"({','.join('?' * len(ids))})", ids).fetchall())


def _qtls(kind: str, col: str, values: list[str]) -> list[dict]:
    ph = ",".join("?" * len(values))
    rows = db().execute(f"SELECT q.gencode, q.variant, q.pvalue, q.tissue, r.rs FROM qtl q "
                        f"LEFT JOIN rsid r ON r.variant=q.variant WHERE q.kind=? AND "
                        f"q.{col} IN ({ph}) ORDER BY q.pvalue", (kind, *values)).fetchall()
    sym = _symbols(r[0] for r in rows)
    return [{"gencodeId": g, "geneSymbol": sym.get(g), "variantId": v, "snpId": rs,
             "pValue": p, "tissueSiteDetailId": t, "ontologyId": _onto(t),
             "datasetId": "gtex_v10"} for g, v, p, t, rs in rows]


@register(HOST, "/api/v2/", "gtex")
def gtex(req: Req) -> Reply | None:
    path = req.path[len("/api/v2/"):].strip("/")
    con = db()
    if path == "dataset/tissueSiteDetail":
        return _data(tissues())
    if path == "reference/gene":
        out = []
        for gid in req.getall("geneId"):
            if gid.upper().startswith("ENSG"):
                rows = con.execute("SELECT gencode, symbol, entrez FROM gene WHERE gencode=? "
                                   "OR gencode LIKE ?", (gid, gid.split(".")[0] + ".%"))
            else:
                rows = con.execute("SELECT gencode, symbol, entrez FROM gene WHERE symbol=? "
                                   "COLLATE NOCASE", (gid,))
            out += [_gene(*r) for r in rows]
        return _data(out)
    if path == "expression/medianGeneExpression":
        ids = req.getall("gencodeId")
        if not ids:
            return None
        ph = ",".join("?" * len(ids))
        rows = con.execute(f"SELECT m.gencode, g.symbol, m.tissue, m.median FROM median m JOIN "
                           f"gene g ON g.gencode=m.gencode WHERE m.gencode IN ({ph})",
                           ids).fetchall()
        return _data([{"gencodeId": g, "geneSymbol": s, "tissueSiteDetailId": t,
                       "ontologyId": _onto(t), "median": v, "unit": "TPM",
                       "datasetId": "gtex_v10"} for g, s, t, v in rows])
    if path in ("association/singleTissueEqtl", "association/singleTissueSqtl"):
        kind = "e" if path.endswith("Eqtl") else "s"
        if req.getall("gencodeId"):
            return _data(_qtls(kind, "gencode", req.getall("gencodeId")))
        if req.getall("variantId"):
            return _data(_qtls(kind, "variant", req.getall("variantId")))
        return None
    if path == "expression/topExpressedGene":
        t = req.get("tissueSiteDetailId")
        if not t:
            return None
        mt = (req.get("filterMtGene") or "true").lower() == "true"
        n = int(req.get("itemsPerPage") or 100)
        rows = con.execute("SELECT m.gencode, g.symbol, m.median FROM median m JOIN gene g ON "
                           "g.gencode=m.gencode WHERE m.tissue=?"
                           + (" AND g.symbol NOT LIKE 'MT-%'" if mt else "")
                           + " ORDER BY m.median DESC LIMIT ?", (t, n)).fetchall()
        return _data([{"gencodeId": g, "geneSymbol": s, "median": v, "tissueSiteDetailId": t,
                       "datasetId": "gtex_v10", "unit": "TPM"} for g, s, v in rows])
    if path == "association/egene":
        t = req.get("tissueSiteDetailId")
        if not t:
            return None
        n = int(req.get("itemsPerPage") or 250)
        rows = con.execute("SELECT e.gencode, g.symbol, e.qvalue FROM egene e LEFT JOIN gene g "
                           "ON g.gencode=e.gencode WHERE e.kind='e' AND e.tissue=? "
                           "ORDER BY e.qvalue LIMIT ?", (t, n)).fetchall()
        return _data([{"gencodeId": g, "geneSymbol": s, "qValue": q, "tissueSiteDetailId": t,
                       "datasetId": "gtex_v10"} for g, s, q in rows])
    if path == "dataset/variant":
        snp = req.get("snpId")
        if not snp:
            return None
        rows = con.execute("SELECT variant, rs FROM rsid WHERE rs=?", (snp.lower(),)).fetchall()
        return _data([{"variantId": v, "snpId": rs, "datasetId": "gtex_v10"} for v, rs in rows])
    return None
