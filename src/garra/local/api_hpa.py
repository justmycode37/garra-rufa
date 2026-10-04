"""Human Protein Atlas (www.proteinatlas.org) from hpa.sqlite:
  /<ENSG>.json                                      full gene record
  /api/search_download.php?search=<symbol>          gene by symbol
  /api/search_download.php?search=<field>:<name>;<categories>   genes enriched in a
      tissue / cell type / brain region (tissue_category_rna, cell_type_category_rna,
      brain_category_rna), highest value first
"""

from __future__ import annotations

import json
import re

from garra.local import connect
from garra.local.router import Reply, Req, register

HOST = "www.proteinatlas.org"
_ENSG = re.compile(r"^/(ENSG\d+)\.json$")


def db():
    return connect("hpa")


@register(HOST, "/ENSG", "hpa")
def gene_json(req: Req) -> Reply | None:
    m = _ENSG.match(req.path)
    if not m:
        return None
    row = db().execute("SELECT doc FROM gene WHERE ensg=?", (m[1],)).fetchone()
    return Reply(json.loads(row[0])) if row else Reply({"error": "not found"}, 404)


@register(HOST, "/api/search_download.php", "hpa")
def search(req: Req) -> Reply | None:
    q = (req.get("search") or "").strip()
    if (req.get("format") or "json") != "json" or not q:
        return None
    con = db()
    m = re.match(r"^(\w+):([^;]+);(.+)$", q)
    if m:
        field, name, cats = m[1], m[2].strip().lower(), [c.strip() for c in m[3].split(",")]
        ph = ",".join("?" * len(cats))
        rows = con.execute(f"SELECT g.symbol, e.ensg FROM enriched e JOIN gene g ON "
                           f"g.ensg=e.ensg WHERE e.field=? AND e.name=? AND e.category IN "
                           f"({ph}) ORDER BY e.value DESC", (field, name, *cats)).fetchall()
    elif re.fullmatch(r"[A-Za-z0-9-]+", q):
        rows = con.execute("SELECT symbol, ensg FROM gene WHERE symbol=? COLLATE NOCASE",
                           (q,)).fetchall()
    else:
        return None
    return Reply([{"Gene": s, "Ensembl": e} for s, e in rows])
