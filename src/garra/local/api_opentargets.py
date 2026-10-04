"""Open Targets Platform GraphQL (POST /api/v4/graphql) from opentargets.sqlite.

The query text is not parsed field by field: the root field (target / disease / drug /
search / mapIds, and disease.evidences) is detected and the full record is returned with
every field the project's queries ask for. GraphQL clients read only what they asked for,
so extra fields are harmless."""

from __future__ import annotations

import json
import re

from garra.local import connect
from garra.local.router import Reply, Req, register

STAGES = ["APPROVAL", "PHASE_4", "PHASE_3", "PHASE_2_3", "PHASE_2", "PHASE_1_2", "PHASE_1",
          "EARLY_PHASE_1"]
_PAGE = re.compile(r"(\w+)\s*\(\s*page\s*:\s*\{[^}]*size\s*:\s*(\$?\w+)")


def db():
    return connect("opentargets")


def _rank(stage: str | None) -> int:
    return STAGES.index(stage) if stage in STAGES else len(STAGES)


def _sizes(query: str, variables: dict) -> dict[str, int]:
    """field name -> page size requested in the query (variables resolved)."""
    out = {}
    for field, size in _PAGE.findall(query):
        v = variables.get(size[1:]) if size.startswith("$") else size
        try:
            out[field] = int(v)
        except (TypeError, ValueError):
            pass
    return out


def _doc(table: str, eid: str) -> dict | None:
    row = db().execute(f"SELECT doc FROM {table} WHERE id=?", (eid,)).fetchone()
    return json.loads(row[0]) if row else None


def _names(table: str, ids) -> dict[str, str]:
    ids = list(dict.fromkeys(ids))
    out = {}
    col = "symbol" if table == "target" else "name"
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        out.update(db().execute(f"SELECT id, {col} FROM {table} WHERE id IN "
                                f"({','.join('?' * len(chunk))})", chunk).fetchall())
    return out


def _candidates(col: str, eid: str) -> list[dict]:
    best: dict[str, str] = {}
    for drug, stage in db().execute(f"SELECT drug, stage FROM candidate WHERE {col}=?", (eid,)):
        if drug not in best or _rank(stage) < _rank(best[drug]):
            best[drug] = stage
    names = _names("drug", best)
    rows = sorted(best.items(), key=lambda kv: (_rank(kv[1]), names.get(kv[0]) or kv[0]))
    return [{"maxClinicalStage": s, "drug": {"id": d, "name": names.get(d, d)}} for d, s in rows]


def target(tid: str, sizes: dict[str, int]) -> dict | None:
    d = _doc("target", tid)
    if not d:
        return None
    n = sizes.get("associatedDiseases", 25)
    rows = db().execute("SELECT disease, score FROM assoc WHERE target=? ORDER BY score DESC "
                        "LIMIT ?", (tid, n)).fetchall()
    dn = _names("disease", [r[0] for r in rows])
    d["associatedDiseases"] = {"count": len(rows), "rows": [
        {"score": s, "disease": {"id": x, "name": dn.get(x, x)}} for x, s in rows]}
    d["drugAndClinicalCandidates"] = {"rows": _candidates("target", tid)}
    inter = d.pop("interactions", [])[:sizes.get("interactions", 25)]
    tn = _names("target", [r["targetB"] for r in inter])
    d["interactions"] = {"count": len(inter), "rows": [
        {"score": r["score"], "sourceDatabase": r["sourceDatabase"],
         "targetB": {"id": r["targetB"], "approvedSymbol": tn.get(r["targetB"], r["targetB"])}}
        for r in inter]}
    expr = d.pop("baselineExpression", [])[:sizes.get("baselineExpression", 25)]
    d["baselineExpression"] = {"count": len(expr), "rows": expr}
    for k in ("safetyLiabilities", "chemicalProbes", "homologues", "tractability",
              "mousePhenotypes", "pharmacogenomics"):
        d.setdefault(k, [])
    return d


def disease(did: str, sizes: dict[str, int]) -> dict | None:
    d = _doc("disease", did)
    if not d:
        alt = db().execute("SELECT id FROM obsolete WHERE old=?", (did,)).fetchone()
        d = _doc("disease", alt[0]) if alt else None
        if not d:
            return None
    did = d["id"]
    n = sizes.get("associatedTargets", 25)
    rows = db().execute("SELECT target, score FROM assoc WHERE disease=? ORDER BY score DESC "
                        "LIMIT ?", (did, n)).fetchall()
    tn = _names("target", [r[0] for r in rows])
    d["associatedTargets"] = {"count": len(rows), "rows": [
        {"score": s, "target": {"id": t, "approvedSymbol": tn.get(t, t)}} for t, s in rows]}
    d["drugAndClinicalCandidates"] = {"rows": _candidates("disease", did)}
    ph = d.pop("phenotypes", [])
    d["phenotypes"] = {"count": len(ph), "rows": ph[:sizes.get("phenotypes", 25)]}
    d["dbXRefs"] = d.get("dbXRefs") or []
    return d


def evidences(did: str, targets: list[str], size: int) -> dict | None:
    d = _doc("disease", did)
    if not d:
        alt = db().execute("SELECT id FROM obsolete WHERE old=?", (did,)).fetchone()
        did = alt[0] if alt else did
    if not targets:
        return {"id": did, "evidences": {"count": 0, "rows": []}}
    ph = ",".join("?" * len(targets))
    rows = db().execute(f"SELECT target, pmid, score FROM lit WHERE disease=? AND target IN "
                        f"({ph}) ORDER BY score DESC LIMIT ?", (did, *targets, size)).fetchall()
    return {"id": did, "evidences": {"count": len(rows), "rows": [
        {"target": {"id": t}, "literature": [p], "score": s} for t, p, s in rows]}}


def drug(cid: str, sizes: dict[str, int]) -> dict | None:
    d = _doc("drug", cid)
    if not d:
        return None
    sym = _names("target", [t for m in d["mechanisms"] for t in m["targets"]])
    d["mechanismsOfAction"] = {"rows": [
        {"actionType": m["actionType"], "mechanismOfAction": m["mechanismOfAction"],
         "targets": [{"id": t, "approvedSymbol": sym.get(t, t)} for t in m["targets"]]}
        for m in d.pop("mechanisms")]}
    best: dict[str, str] = {}
    for dis, stage in db().execute("SELECT disease, stage FROM candidate WHERE drug=? AND "
                                   "disease IS NOT NULL", (cid,)):
        if dis not in best or _rank(stage) < _rank(best[dis]):
            best[dis] = stage
    dn = _names("disease", best)
    d["indications"] = {"rows": [{"maxClinicalStage": s, "disease": {"id": x,
                                                                      "name": dn.get(x, x)}}
                                 for x, s in sorted(best.items(), key=lambda kv: _rank(kv[1]))]}
    ae = d.pop("adverseEvents", [])
    d["adverseEvents"] = {"count": len(ae), "rows": ae[:sizes.get("adverseEvents", 25)]}
    return d


def search(q: str, entities: list[str] | None, n: int = 25) -> list[dict]:
    words = re.findall(r"\w+", q)
    if not words:
        return []
    con = db()
    hits = []
    for term, entity, eid in con.execute("SELECT term, entity, id FROM alias WHERE term=?",
                                         (q.strip().upper(),)):
        if entity in ("target", "disease", "drug"):
            hits.append((entity, eid))
    for entity, eid in con.execute(
            "SELECT entity, id FROM name_fts WHERE name_fts MATCH ? ORDER BY bm25(name_fts) "
            "LIMIT ?", (" ".join(f'"{w}"' for w in words), n * 3)):
        hits.append((entity, eid))
    out = []
    for entity, eid in dict.fromkeys(hits):
        if entities and entity not in entities:
            continue
        table = entity
        name = _names(table, [eid]).get(eid, eid)
        out.append({"id": eid, "name": name, "entity": entity})
        if len(out) >= n:
            break
    return out


def map_ids(terms: list[str], entities: list[str]) -> list[dict]:
    out = []
    for t in terms:
        hits = []
        rows = db().execute("SELECT entity, id FROM alias WHERE term=?",
                            (t.strip().upper(),)).fetchall()
        rows.sort(key=lambda r: r[0] != "target")  # approved symbols before synonyms
        for entity, eid in rows:
            ent = "target" if entity == "target_synonym" else entity
            if entities and ent not in entities:
                continue
            table = ent
            hits.append({"id": eid, "name": _names(table, [eid]).get(eid, eid), "entity": ent})
        out.append({"term": t, "hits": list({h["id"]: h for h in hits}.values())})
    return out


@register("api.platform.opentargets.org", "/api/v4/graphql", "opentargets")
def graphql(req: Req) -> Reply | None:
    body = req.json() or {}
    q = body.get("query") or req.get("query") or ""
    v = body.get("variables") or {}
    sizes = _sizes(q, v)

    def arg(name: str) -> str | None:
        m = re.search(name + r"\s*:\s*(\$\w+|\"[^\"]*\")", q)
        if not m:
            return None
        tok = m[1]
        return v.get(tok[1:]) if tok.startswith("$") else tok.strip('"')

    if re.search(r"\bsearch\s*\(", q):
        ents = None
        m = re.search(r"entityNames\s*:\s*\[([^\]]*)\]", q)
        if m:
            ents = re.findall(r'"(\w+)"', m[1])
        return Reply({"data": {"search": {"hits": search(arg("queryString") or "", ents)}}})
    if re.search(r"\bmapIds\s*\(", q):
        terms = v.get("terms") or v.get("t") or []
        ents = v.get("e") or v.get("entityNames")
        if ents is None:
            m = re.search(r"entityNames\s*:\s*\[([^\]]*)\]", q)
            ents = re.findall(r'"(\w+)"', m[1]) if m else []
        return Reply({"data": {"mapIds": {"mappings": map_ids(terms, ents)}}})
    if re.search(r"\btarget\s*\(\s*ensemblId", q):
        return Reply({"data": {"target": target(arg("ensemblId") or "", sizes)}})
    if re.search(r"\bdisease\s*\(\s*efoId", q):
        did = arg("efoId") or ""
        if re.search(r"\bevidences\s*\(", q):
            m = re.search(r"ensemblIds\s*:\s*(\$\w+)", q)
            tids = v.get(m[1][1:]) if m else []
            sm = re.search(r"evidences\s*\([^)]*size\s*:\s*(\d+)", q)
            return Reply({"data": {"disease": evidences(did, tids or [],
                                                        int(sm[1]) if sm else 500)}})
        return Reply({"data": {"disease": disease(did, sizes)}})
    if re.search(r"\bdrug\s*\(\s*chemblId", q):
        return Reply({"data": {"drug": drug(arg("chemblId") or "", sizes)}})
    return None
