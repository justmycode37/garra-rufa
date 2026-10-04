"""NIH RePORTER (POST https://api.reporter.nih.gov/v2/projects/search) from reporter.sqlite:
criteria.advanced_text_search (operator and/or, search_field all) or criteria.
text_search; newest fiscal year first, best match within a year. Other criteria fall
back to the API."""

from __future__ import annotations

import re

from garra.local import connect
from garra.local.router import Reply, Req, register

_WORD = re.compile(r"\w+", re.UNICODE)


@register("api.reporter.nih.gov", "/v2/projects/search", "reporter")
def search(req: Req) -> Reply | None:
    body = req.json() or {}
    crit = body.get("criteria") or {}
    ats = crit.get("advanced_text_search") or {}
    if set(crit) - {"advanced_text_search"} or not ats.get("search_text"):
        return None
    if (ats.get("search_field") or "all") not in ("all", "projecttitle,terms,abstracttext",
                                                  "projecttitle,abstracttext"):
        return None
    phrases = re.findall(r'"([^"]+)"', ats["search_text"])
    words = [w for w in _WORD.findall(re.sub(r'"[^"]*"', " ", ats["search_text"]))
             if w.upper() not in ("AND", "OR", "NOT")]
    parts = [f'"{" ".join(_WORD.findall(p))}"' for p in phrases] + [f'"{w}"' for w in words]
    if not parts:
        return None
    op = " OR " if (ats.get("operator") or "and").lower() == "or" else " AND "
    q = op.join(parts)
    limit = int(body.get("limit") or 50)
    offset = int(body.get("offset") or 0)
    con = connect("reporter")
    total = con.execute("SELECT count(*) FROM project_fts WHERE project_fts MATCH ?",
                        (q,)).fetchone()[0]
    rows = con.execute(
        "SELECT p.appl_id, p.project_num, p.fy, p.title, p.pi, p.ic, p.ic_name, p.org, "
        "p.abstract FROM project_fts f JOIN project p ON p.appl_id=f.appl_id "
        "WHERE project_fts MATCH ? ORDER BY p.fy DESC, bm25(project_fts, 4.0, 1.0) "
        "LIMIT ? OFFSET ?", (q, limit, offset)).fetchall()
    results = [{"appl_id": int(a) if a.isdigit() else a, "project_num": num, "fiscal_year": fy,
                "project_title": title, "contact_pi_name": pi,
                "agency_ic_admin": {"abbreviation": ic, "name": ic_name},
                "organization": {"org_name": org},
                "project_detail_url": f"https://reporter.nih.gov/project-details/{a}",
                "abstract_text": abstract}
               for a, num, fy, title, pi, ic, ic_name, org, abstract in rows]
    return Reply({"meta": {"total": total, "offset": offset, "limit": limit},
                  "results": results})
