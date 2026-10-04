"""JAX HPO API (https://ontology.jax.org/api) from ontology.sqlite + hpo.sqlite.

  /hp/search?q=                     phenotype term search
  /hp/terms/{HP}[/parents|children] term record / hierarchy
  /network/search/disease|gene?q=   disease / gene search
  /network/annotation/{id}          HP term, OMIM/ORPHA/DECIPHER disease or NCBIGene gene
"""

from __future__ import annotations

from urllib.parse import unquote

from garra.local import connect
from garra.local import ontology as O
from garra.local.router import Reply, Req, register

HOST = "ontology.jax.org"


def _hpo():
    return connect("hpo")


def _term_brief(c: str) -> dict:
    return {"id": c, "name": O.label(c) or c}


@register(HOST, "/api/hp/", "ontology")
def hp(req: Req) -> Reply | None:
    parts = [unquote(p) for p in req.path[len("/api/hp/"):].split("/")]
    if parts[0] == "search":
        q = req.get("q") or ""
        n = int(req.get("max") or req.get("limit") or 10)
        hits = O.search(q, "hp", n)
        terms = [{"id": c, "name": lab, "xrefs": O.xrefs(c),
                  "synonyms": [s for s, _, _ in O.synonyms(c)]} for c, lab in hits]
        return Reply({"terms": terms, "count": len(terms)})
    if parts[0] != "terms" or len(parts) < 2:
        return None
    t = O.term(parts[1])
    if not t or t["ont"] != "hp":
        return Reply({"message": "not found"}, 404)
    c = t["curie"]
    if len(parts) == 2:
        return Reply({"id": c, "name": t["label"], "definition": t["definition"],
                      "comment": t["comment"], "synonyms": [s for s, _, _ in O.synonyms(c)],
                      "xrefs": O.xrefs(c), "publicationReferences": t["def_refs"]})
    if parts[2] == "parents":
        return Reply([_term_brief(p) for p in O.parents(c)])
    if parts[2] == "children":
        return Reply([_term_brief(x) for x in O.children(c)])
    return None


def _disease(d_id: str, name: str | None, mondo: str | None) -> dict:
    return {"id": d_id, "name": name, "mondoId": mondo}


def _diseases(rows) -> list[dict]:
    return [_disease(*r) for r in rows]


@register(HOST, "/api/network/", ("ontology", "hpo"))
def network(req: Req) -> Reply | None:
    parts = [unquote(p) for p in req.path[len("/api/network/"):].split("/")]
    con = _hpo()
    if parts[:2] == ["search", "disease"]:
        q = (req.get("q") or "").strip()
        n = int(req.get("limit") or 10)
        rows = con.execute("SELECT id, name, mondo FROM disease WHERE lower(name)=lower(?)",
                           (q,)).fetchall()
        words = [w for w in q.replace('"', " ").split() if w]
        if words and len(rows) < n:
            rows += [r for r in con.execute(
                "SELECT d.id, d.name, d.mondo FROM disease_fts f JOIN disease d ON d.id=f.id "
                "WHERE disease_fts MATCH ? ORDER BY bm25(disease_fts) LIMIT ?",
                (" ".join(f'"{w}"' for w in words), n * 2)) if r not in rows]
        if q.upper().startswith("MONDO:"):
            rows = con.execute("SELECT id, name, mondo FROM disease WHERE mondo=?",
                               (q.upper(),)).fetchall()
        res = _diseases(rows[:n])
        return Reply({"results": res, "totalCount": len(res)})
    if parts[:2] == ["search", "gene"]:
        q = (req.get("q") or "").strip()
        n = int(req.get("limit") or 10)
        rows = con.execute("SELECT gene, symbol FROM gene WHERE symbol=? COLLATE NOCASE "
                           "UNION ALL SELECT gene, symbol FROM gene WHERE symbol LIKE ? "
                           "AND symbol!=? COLLATE NOCASE LIMIT ?",
                           (q, q + "%", q, n)).fetchall()
        res = [{"id": g, "name": s} for g, s in rows[:n]]
        return Reply({"results": res, "totalCount": len(res)})
    if parts[0] != "annotation" or len(parts) != 2:
        return None
    eid = parts[1].replace("ORPHANET:", "ORPHA:")
    prefix = eid.split(":", 1)[0].upper()
    if prefix == "HP":
        t = O.term(eid)
        if not t:
            return Reply({"message": "not found"}, 404)
        desc = [eid] + [r[0] for r in O.db().execute(
            "WITH RECURSIVE d(c) AS (SELECT ? UNION SELECT e.subj FROM edge e JOIN d "
            "ON e.obj=d.c WHERE e.rel='is_a') SELECT c FROM d", (t["curie"],))][1:]
        ph = ",".join("?" * len(desc))
        dis = con.execute(
            f"SELECT d.id, d.name, d.mondo FROM disease d WHERE d.id IN (SELECT disease FROM "
            f"dpheno WHERE hp IN ({ph})) ORDER BY d.id IN (SELECT disease FROM dpheno WHERE "
            f"hp=?) DESC, d.name", (*desc, t["curie"])).fetchall()
        genes = con.execute(
            f"SELECT g.gene, g.symbol FROM gene g WHERE g.gene IN (SELECT gene FROM gpheno "
            f"WHERE hp IN ({ph})) ORDER BY g.symbol", desc).fetchall()
        return Reply({"diseases": _diseases(dis), "genes": [{"id": g, "name": s}
                                                             for g, s in genes]})
    if prefix in ("OMIM", "ORPHA", "DECIPHER"):
        row = con.execute("SELECT id, name, mondo FROM disease WHERE id=?", (eid,)).fetchone()
        if not row:
            return Reply({"message": "not found"}, 404)
        mondo = row[2]
        desc = None
        if mondo:
            mt = O.term(mondo)
            desc = mt["definition"] if mt else None
        genes = con.execute("SELECT DISTINCT gene, symbol FROM gdisease WHERE disease=?",
                            (eid,)).fetchall()
        cats: dict[str, list[dict]] = {}
        for hp_, freq, onset, sex, refs, cat in con.execute(
                "SELECT hp, freq, onset, sex, refs, category FROM dpheno WHERE disease=? "
                "AND aspect='P'", (eid,)):
            cats.setdefault(cat or "Other", []).append({
                "id": hp_, "name": O.label(hp_) or hp_,
                "metadata": {"frequency": freq, "onset": onset, "sex": sex,
                             "sources": (refs or "").split(";") if refs else []}})
        return Reply({"disease": {**_disease(*row), "description": desc},
                      "genes": [{"id": g, "name": s} for g, s in genes],
                      "categories": cats})
    if prefix == "NCBIGENE":
        g = "NCBIGene:" + eid.split(":", 1)[1]
        row = con.execute("SELECT gene, symbol FROM gene WHERE gene=?", (g,)).fetchone()
        if not row:
            return Reply({"message": "not found"}, 404)
        dis = con.execute("SELECT DISTINCT d.id, d.name, d.mondo FROM gdisease x JOIN disease d "
                          "ON d.id=x.disease WHERE x.gene=?", (g,)).fetchall()
        phen = [r[0] for r in con.execute("SELECT hp FROM gpheno WHERE gene=?", (g,))]
        return Reply({"gene": {"id": row[0], "name": row[1]}, "diseases": _diseases(dis),
                      "phenotypes": [_term_brief(h) for h in phen]})
    return None
