"""EBI OLS4 (https://www.ebi.ac.uk/ols4/api) from ontology.sqlite.

  /search                                          v1 search (exact or free text)
  /ontologies/{ont}/terms?obo_id=|iri=             v1 term lookup by id
  /ontologies/{ont}/terms/{iri}[/parents|children|<relation>]   v1 term record and links
  /v2/ontologies/{ont}/classes/{iri}[/children|/relatedFrom]   v2 class record
Orphanet ("ordo") searches are answered from orphadata.sqlite when it exists.
"""

from __future__ import annotations

from urllib.parse import quote, unquote

from garra.local import available
from garra.local import ontology as O
from garra.local.router import Reply, Req, register

HOST = "www.ebi.ac.uk"
BASE = "https://www.ebi.ac.uk/ols4/api"
SCOPE = {"EXACT": "hasExactSynonym", "RELATED": "hasRelatedSynonym",
         "NARROW": "hasNarrowSynonym", "BROAD": "hasBroadSynonym"}
SKIP_LINKS = {"excluded_subclassof", "curated_content_resource"}
XREF_KEY = "http://www.geneontology.org/formats/oboInOwl#hasDbXref"
DEFINITION = "http://purl.obolibrary.org/obo/IAO_0000115"
EXACT_SYN = "http://www.geneontology.org/formats/oboInOwl#hasExactSynonym"
REL_IRI = "https://local.garra/relation/"


def _ont_ok(ont: str) -> bool:
    return ont in O.PREFIX_OF


def _doc(c: str, lab: str, ont: str) -> dict:
    return {"obo_id": c, "label": lab, "iri": O.iri(c), "ontology_name": ont,
            "short_form": c.replace(":", "_"), "type": "class"}


def _ordo_search(q: str, exact: bool, rows: int) -> list[dict] | None:
    if not available("orphadata"):
        return None
    from garra.local import orphadata
    hits = orphadata.find_name(q) if exact else orphadata.search_names(q, rows)
    return [{"obo_id": f"Orphanet:{code}", "label": name, "ontology_name": "ordo",
             "iri": f"http://www.orpha.net/ORDO/Orphanet_{code}"} for code, name in hits[:rows]]


@register(HOST, "/ols4/api/search", "ontology")
def search(req: Req) -> Reply | None:
    q = (req.get("q") or "").strip()
    onts = req.getall("ontology")
    if not q or len(onts) != 1:
        return None
    ont = onts[0].lower()
    exact = (req.get("exact") or "false").lower() == "true"
    rows = int(req.get("rows") or 10)
    if ont == "ordo":
        docs = _ordo_search(q, exact, rows)
        if docs is None:
            return None
    elif _ont_ok(ont):
        obs = (req.get("obsoletes") or "false").lower() == "true"
        hits = O.exact(q, ont, rows) if exact else O.search(q, ont, rows, obsoletes=obs)
        docs = [_doc(c, lab, ont) for c, lab in hits]
        fields = {f for v in req.getall("fieldList") for f in v.split(",")}
        if "exact_synonyms" in fields:  # as live OLS: asked for, not in the default docs
            for d in docs:
                d["exact_synonyms"] = [n for n, sc, _ in O.synonyms(d["obo_id"])
                                       if sc == "EXACT"]
    else:
        return None
    return Reply({"response": {"docs": docs, "numFound": len(docs), "start": 0},
                  "responseHeader": {"status": 0, "QTime": 0}})


# -- v1 term records ------------------------------------------------------------------
def _v1_term(c: str, ont: str) -> dict | None:
    t = O.term(c)
    if not t or t["ont"] != ont:
        return None
    c = t["curie"]
    syns = O.synonyms(c)
    refs = t["def_refs"]
    xrefs_ = []
    for r in refs:
        if "://" in r:
            xrefs_.append({"database": None, "id": None, "url": r})
        else:
            db, _, i = r.partition(":")
            xrefs_.append({"database": db, "id": i, "url": None})
    ann: dict[str, list[str]] = {"database_cross_reference": O.xrefs(c)}
    for rel, obj, _ in O.relations_out(c):
        if rel == "curated_content_resource":
            ann.setdefault("curated content resource", []).append(obj)
    for k, v in O.annotations(c):
        if k in ("rdfs:seeAlso", "seeAlso"):
            ann.setdefault("seeAlso", []).append(v)
    ann["subset"] = O.subsets(c)
    self_url = f"{BASE}/ontologies/{ont}/terms/{quote(quote(O.iri(c), safe=''), safe='')}"
    links = {"self": {"href": self_url}, "parents": {"href": self_url + "/parents"},
             "children": {"href": self_url + "/children"}}
    for rel in dict.fromkeys(r for r, _, _ in O.relations_out(c)):
        if rel not in SKIP_LINKS:
            links[rel] = {"href": f"{self_url}/{rel}"}
    return {
        "iri": O.iri(c), "label": t["label"], "obo_id": c, "short_form": c.replace(":", "_"),
        "ontology_name": ont, "is_obsolete": t["obsolete"],
        "description": [t["definition"]] if t["definition"] else [],
        "synonyms": [n for n, s, _ in syns if s == "EXACT"],
        "obo_synonym": [{"name": n, "scope": SCOPE.get(s, s), "type": (ty or "").lower() or None,
                         "xrefs": []} for n, s, ty in syns],
        "obo_definition_citation": [{"definition": t["definition"], "oboXrefs": xrefs_}]
        if t["definition"] else [],
        "obo_xref": [{"database": x.split(":", 1)[0], "id": x.split(":", 1)[-1]}
                     for x in O.xrefs(c)],
        "annotation": ann, "_links": links,
    }


def _embedded(curies: list[tuple[str, str | None]]) -> dict:
    terms = []
    for c, fallback in curies:
        lab = O.label(c) or fallback or c
        terms.append({"iri": O.iri(c), "label": lab, "obo_id": c if ":" in c else None,
                      "short_form": c.replace(":", "_")})
    return {"_embedded": {"terms": terms}, "page": {"size": len(terms),
                                                     "totalElements": len(terms)}}


def _paged(items: list, req: Req) -> list:
    size = int(req.get("size") or 20)
    page = int(req.get("page") or 0)
    return items[page * size:(page + 1) * size]


@register(HOST, "/ols4/api/ontologies/", "ontology")
def v1_terms(req: Req) -> Reply | None:
    parts = req.path[len("/ols4/api/ontologies/"):].split("/")
    if len(parts) < 2 or parts[1] != "terms" or not _ont_ok(parts[0].lower()):
        return None
    ont = parts[0].lower()
    if len(parts) == 2:  # ?obo_id= / ?iri=
        c = req.get("obo_id") or (O.curie(req.get("iri") or "") if req.get("iri") else None)
        rec = _v1_term(c, ont) if c else None
        if not rec:
            return Reply({"page": {"totalElements": 0}}, 404) if c else None
        return Reply({"_embedded": {"terms": [rec]}, "page": {"totalElements": 1}})
    c = O.curie(unquote(unquote(parts[2])))
    if not c:
        return None
    rec = _v1_term(c, ont)
    if not rec:
        return Reply({"error": "Not Found", "status": 404}, 404)
    if len(parts) == 3:
        return Reply(rec)
    link = parts[3]
    c = rec["obo_id"]
    if link == "parents":
        items = [(p, None) for p in O.parents(c)]
    elif link == "children":
        items = [(x, None) for x in O.children(c)]
    else:
        items = [(o, lab) for r, o, lab in O.relations_out(c) if r == link]
    return Reply(_embedded(_paged(items, req)))


# -- v2 class records ----------------------------------------------------------------
def _v2_class(c: str, ont: str) -> dict | None:
    t = O.term(c)
    if not t or t["ont"] != ont:
        return None
    c = t["curie"]
    linked: dict[str, dict] = {}

    def link(x: str, fallback: str | None = None) -> str:
        i = O.iri(x)
        linked.setdefault(i, {"label": O.label(x) or fallback or x, "curie": x})
        return i

    related = []
    for rel, obj, lab in O.relations_out(c):
        if rel in SKIP_LINKS:
            continue
        p = REL_IRI + rel
        linked.setdefault(p, {"label": rel, "curie": rel})
        related.append({"property": p, "value": link(obj, lab)})
    defn = t["definition"]
    return {
        "iri": O.iri(c), "label": t["label"], "curie": c, "ontologyId": ont,
        "directParent": [link(p) for p in O.parents(c)],
        "relatedTo": related,
        XREF_KEY: [x for x in O.xrefs(c)],
        DEFINITION: ({"type": ["reification"], "value": defn,
                      "axioms": [{XREF_KEY: t["def_refs"]}]} if t["def_refs"] else defn)
        if defn else None,
        EXACT_SYN: [n for n, s, _ in O.synonyms(c) if s == "EXACT"],
        "linkedEntities": linked,
    }


@register(HOST, "/ols4/api/v2/ontologies/", "ontology")
def v2_classes(req: Req) -> Reply | None:
    parts = req.path[len("/ols4/api/v2/ontologies/"):].split("/")
    if len(parts) < 3 or parts[1] != "classes" or not _ont_ok(parts[0].lower()):
        return None
    ont = parts[0].lower()
    c = O.curie(unquote(unquote(parts[2])))
    if not c:
        return None
    rec = _v2_class(c, ont)
    if not rec:
        return Reply({"error": "Not Found"}, 404)
    if len(parts) == 3:
        return Reply(rec)
    c = rec["curie"]
    if parts[3] == "children":
        els = [{"iri": O.iri(x), "curie": x, "label": O.label(x)} for x in O.children(c)]
    elif parts[3] == "relatedFrom":
        els = []
        for rel, subj in O.relations_in(c):
            if rel in SKIP_LINKS:
                continue
            p = REL_IRI + rel
            els.append({"iri": O.iri(subj), "curie": subj, "label": O.label(subj) or subj,
                        "relatedTo": [{"property": p, "value": rec["iri"]}],
                        "linkedEntities": {p: {"label": rel}}})
    else:
        return None
    size = int(req.get("size") or 20)
    return Reply({"elements": els[:size], "totalElements": len(els)})
