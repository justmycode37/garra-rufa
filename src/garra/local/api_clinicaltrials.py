"""ClinicalTrials.gov API v2 (GET /api/v2/studies) from clinicaltrials.sqlite.

  query.term=AREA[ConditionMeshId]D008382   studies whose condition MeSH list has the id
  query.cond=<text>                         conditions / keywords / condition MeSH terms
  query.term=<text>                         conditions, titles and brief summaries
  sort=LastUpdatePostDate:desc, pageSize    (other sorts: best match)
Studies keep the modules the project reads (see build_clinicaltrials.py); `fields` is
ignored, so callers get a superset of what they asked for."""

from __future__ import annotations

import json
import re
import zlib

from garra.local import connect
from garra.local.router import Reply, Req, register

_AREA = re.compile(r"^AREA\[ConditionMeshId\]\s*([DC]\d+)$", re.I)
_WORD = re.compile(r"\w+", re.UNICODE)


def db():
    return connect("clinicaltrials")


def _fts(text: str, column: str | None) -> str | None:
    phrases = re.findall(r'"([^"]+)"', text)
    rest = re.sub(r'"[^"]*"', " ", text)
    parts = [" ".join(_WORD.findall(p)) for p in phrases]
    parts += _WORD.findall(rest)
    parts = [p for p in parts if p and p.upper() not in ("AND", "OR", "NOT")]
    if not parts:
        return None
    q = " AND ".join(f'"{p}"' for p in parts)
    return f"{{{column}}} : ({q})" if column else q


@register("clinicaltrials.gov", "/api/v2/studies", "clinicaltrials")
def studies(req: Req) -> Reply | None:
    if req.path.rstrip("/") != "/api/v2/studies" or req.get("pageToken"):
        return None
    term, cond = (req.get("query.term") or "").strip(), (req.get("query.cond") or "").strip()
    if (term and cond) or not (term or cond) or any(
            k.startswith(("query.", "filter.")) and k not in ("query.term", "query.cond")
            for k in req.params):
        return None
    size = min(int(req.get("pageSize") or 10), 1000)
    by_date = (req.get("sort") or "").lower().startswith("lastupdatepostdate")
    con = db()
    m = _AREA.match(term) if term else None
    if m:
        rows = con.execute("SELECT s.nct, s.doc FROM cmesh c JOIN study s ON s.nct=c.nct "
                           "WHERE c.mesh=? GROUP BY s.nct ORDER BY s.updated DESC LIMIT ?",
                           (m[1].upper(), size)).fetchall()
        total = con.execute("SELECT count(DISTINCT nct) FROM cmesh WHERE mesh=?",
                            (m[1].upper(),)).fetchone()[0]
    else:
        if term.upper().startswith("AREA["):
            return None
        q = _fts(cond, "cond") if cond else _fts(term, None)
        if not q:
            return None
        order = "s.updated DESC" if by_date else "bm25(study_fts, 5.0, 2.0, 1.0)"
        rows = con.execute(f"SELECT s.nct, s.doc FROM study_fts f JOIN study s ON s.nct=f.nct "
                           f"WHERE study_fts MATCH ? ORDER BY {order} LIMIT ?",
                           (q, size)).fetchall()
        total = con.execute("SELECT count(*) FROM study_fts WHERE study_fts MATCH ?",
                            (q,)).fetchone()[0]
    docs = [json.loads(zlib.decompress(doc)) for _, doc in rows]
    out = {"studies": docs}
    if (req.get("countTotal") or "").lower() == "true":
        out["totalCount"] = total
    return Reply(out)
