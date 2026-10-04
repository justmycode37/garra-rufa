"""Monarch API v3 (/v3/api/search, /entity/{id}, /association) from monarch.sqlite.
The semantic-similarity endpoint (/semsim) is not emulated and still goes to the API."""

from __future__ import annotations

import json
import re
from urllib.parse import unquote

from garra.local import available, connect
from garra.local.router import Reply, Req, register

HOSTS = ("api-v3.monarchinitiative.org", "api.monarchinitiative.org")
CURIE = re.compile(r"^[A-Za-z][\w.]*:\S+$")
MAX_DESCENDANTS = 3000


def db():
    return connect("monarch")


def _node(nid: str) -> dict | None:
    row = db().execute("SELECT id, name, category, description, full_name, symbol, xref, "
                       "exact_synonym, in_taxon FROM node WHERE id=?", (nid,)).fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "category": row[2], "description": row[3],
            "full_name": row[4], "symbol": row[5], "xref": json.loads(row[6] or "[]"),
            "exact_synonym": json.loads(row[7] or "[]"), "in_taxon": row[8]}


def _brief(nid: str) -> dict:
    n = _node(nid)
    return {"id": nid, "name": n["name"] if n else None,
            "category": n["category"] if n else None}


def _item(n: dict) -> dict:
    return {**n, "synonym": n.get("exact_synonym")}


def search_items(q: str, limit: int) -> list[dict]:
    con = db()
    q = q.strip()
    ids: list[str] = []
    if CURIE.match(q):
        if _node(q):
            ids.append(q)
        ids += [r[0] for r in con.execute("SELECT id FROM nxref WHERE xref=? COLLATE NOCASE",
                                          (q,))]
    else:
        ids += [r[0] for r in con.execute(
            "SELECT id FROM node WHERE lower(name)=lower(?) AND (id LIKE 'MONDO:%' OR "
            "id LIKE 'HP:%' OR id LIKE 'HGNC:%') ORDER BY id LIKE 'MONDO:%' DESC", (q,))]
        words = re.findall(r"\w+", q)
        if words:
            ids += [r[0] for r in con.execute(
                "SELECT id FROM node_fts WHERE node_fts MATCH ? ORDER BY bm25(node_fts, 4.0, 1.0)"
                " LIMIT ?", (" ".join(f'"{w}"' for w in words), limit * 3))]
    out = []
    for nid in dict.fromkeys(ids):
        n = _node(nid)
        if n and not (n["name"] or "").lower().startswith("obsolete "):
            out.append(_item(n))
        if len(out) >= limit:
            break
    return out


def _hierarchy(nid: str) -> dict:
    con = db()
    sup = [r[0] for r in con.execute("SELECT parent FROM sub WHERE child=?", (nid,))]
    subs = [r[0] for r in con.execute("SELECT child FROM sub WHERE parent=?", (nid,))]
    return {"super_classes": [_brief(x) for x in sup], "sub_classes": [_brief(x) for x in subs]}


def _descendants(nid: str) -> list[str]:
    rows = db().execute("WITH RECURSIVE d(c) AS (SELECT ? UNION SELECT s.child FROM sub s "
                        "JOIN d ON s.parent=d.c) SELECT c FROM d LIMIT ?",
                        (nid, MAX_DESCENDANTS)).fetchall()
    return [r[0] for r in rows]


def _freq_label(q: str | None) -> str | None:
    if not q or not available("ontology"):
        return None
    from garra.local import ontology
    return ontology.label(q)


def associations(side: str, nid: str, category: str | None, direct: bool, limit: int,
                 offset: int = 0) -> tuple[list[dict], int]:
    ids = [nid] if direct else _descendants(nid)
    ph = ",".join("?" * len(ids))
    where = f"e.{side} IN ({ph}) AND e.negated=0"
    args: list = list(ids)
    if category:
        where += " AND e.category=?"
        args.append(category)
    con = db()
    total = con.execute(f"SELECT count(*) FROM edge e WHERE {where}", args).fetchone()[0]
    rows = con.execute(
        f"SELECT e.subject, e.object, e.category, e.predicate, e.publications, "
        f"e.has_percentage, e.has_count, e.has_total, e.frequency_qualifier, "
        f"e.onset_qualifier, e.knowledge_source, s.name, s.category, o.name, o.category "
        f"FROM edge e LEFT JOIN node s ON s.id=e.subject LEFT JOIN node o ON o.id=e.object "
        f"WHERE {where} ORDER BY e.{side}=? DESC, json_array_length(e.publications) DESC, "
        f"o.name, s.name LIMIT ? OFFSET ?", (*args, nid, limit, offset)).fetchall()
    items = []
    for (s, o, cat, pred, pubs, pct, cnt, tot, fq, onset, ks, sn, sc, on, oc) in rows:
        items.append({"subject": s, "subject_label": sn, "subject_category": sc,
                      "object": o, "object_label": on, "object_category": oc,
                      "predicate": pred, "category": cat,
                      "publications": json.loads(pubs or "[]"), "has_percentage": pct,
                      "has_count": cnt, "has_total": tot, "frequency_qualifier": fq,
                      "frequency_qualifier_label": _freq_label(fq),
                      "onset_qualifier": onset, "primary_knowledge_source": ks,
                      "negated": False})
    return items, total


def _handler(req: Req) -> Reply | None:
    path = req.path.split("/v3/api", 1)[-1]
    if path == "/search":
        limit = int(req.get("limit") or 20)
        items = search_items(req.get("q") or "", limit)
        return Reply({"items": items, "total": len(items), "limit": limit, "offset": 0})
    if path.startswith("/entity/"):
        nid = unquote(path[len("/entity/"):])
        n = _node(nid)
        if not n:
            return Reply({"detail": "Entity not found"}, 404)
        return Reply({**n, "node_hierarchy": _hierarchy(nid)})
    if path == "/association":
        subj, obj = req.get("subject"), req.get("object")
        if bool(subj) == bool(obj):
            return None
        cats = req.getall("category")
        if len(cats) > 1:
            return None
        limit = int(req.get("limit") or 20)
        offset = int(req.get("offset") or 0)
        direct = (req.get("direct") or "false").lower() == "true"
        side, nid = ("subject", subj) if subj else ("object", obj)
        items, total = associations(side, nid, cats[0] if cats else None, direct, limit, offset)
        return Reply({"items": items, "total": total, "limit": limit, "offset": offset})
    return None


for _host in HOSTS:
    register(_host, "/v3/api/search", "monarch")(_handler)
    register(_host, "/v3/api/entity/", "monarch")(_handler)
    register(_host, "/v3/api/association", "monarch")(_handler)
