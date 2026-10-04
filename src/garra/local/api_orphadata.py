"""Orphanet APIs from orphadata.sqlite:
  api.orphacode.org/EN/ClinicalEntity/...   nomenclature (names, definition, hierarchy, OMIM)
  api.orphadata.com/rd-...                  phenotypes, genes, classifications, xrefs
"""

from __future__ import annotations

from urllib.parse import unquote

from garra.local import orphadata as D
from garra.local.router import Reply, Req, register

NOT_FOUND = Reply({"detail": "Not found"}, 404)


def _code(code) -> int | str:
    return int(code) if str(code).isdigit() else code


def _ref(code) -> dict:
    return {"ORPHAcode": _code(code), "Preferred term": D.name(code)}


@register("api.orphacode.org", "/EN/ClinicalEntity/", "orphadata")
def orphacode(req: Req) -> Reply | None:
    parts = [unquote(p) for p in req.path[len("/EN/ClinicalEntity/"):].split("/")]
    op = parts[0]
    if op == "FindbyName" and len(parts) == 2:
        hits = [h for h in D.find_name(parts[1]) if h[1].lower() == parts[1].strip().lower()] \
            or D.find_name(parts[1])
        return Reply(_ref(hits[0][0])) if hits else NOT_FOUND
    if op == "ApproximateName" and len(parts) == 2:
        hits = D.search_names(parts[1], 50)
        return Reply([_ref(c) for c, _ in hits]) if hits else NOT_FOUND
    if op == "FindbyOMIM" and len(parts) == 2:
        refs = [{**_ref(c), "DisorderMappingRelation": rel}
                for c, _, rel in D.by_xref("OMIM", parts[1])]
        return Reply({"OMIM": parts[1], "References": refs}) if refs else NOT_FOUND
    if op != "orphacode" or len(parts) < 3:
        return None
    code, what = parts[1], parts[2]
    d = D.disorder(code)
    if not d:
        return NOT_FOUND
    base = {"ORPHAcode": _code(code), "Preferred term": d["name"]}
    if what == "Name":
        return Reply(base)
    if what == "Definition":
        return Reply({**base, "Definition": d["definition"]}) if d["definition"] else NOT_FOUND
    if what == "PreferentialParent":
        p = D.preferential_parent(code)
        return Reply({**base, "Preferential parent": _ref(p)}) if p else NOT_FOUND
    if what == "PreferentialChildren":
        kids = D.preferential_children(code)
        return Reply([_ref(c) for c in kids]) if kids else NOT_FOUND
    return None


def _data(results) -> Reply:
    return Reply({"data": {"results": results}})


def _pheno_assoc(code: str) -> list[dict]:
    return [{"HPO": {"HPOId": hp, "HPOTerm": term}, "HPOFrequency": freq,
             "DiagnosticCriteria": crit} for hp, term, freq, crit in D.phenotypes(code)]


def _disorder_block(code: str, **extra) -> dict:
    return {"ORPHAcode": _code(code), "Preferred term": D.name(code), **extra}


@register("api.orphadata.com", "/rd-", "orphadata")
def orphadata(req: Req) -> Reply | None:
    parts = [unquote(p) for p in req.path.strip("/").split("/")]
    product = parts[0]
    if product == "rd-phenotypes" and len(parts) == 3:
        if parts[1] == "orphacodes":
            assoc = _pheno_assoc(parts[2])
            if not assoc:
                return NOT_FOUND
            return _data(_disorder_block(parts[2], Disorder=_disorder_block(
                parts[2], HPODisorderAssociation=assoc)))
        if parts[1] == "hpoids":
            codes = D.by_phenotype(parts[2])
            if not codes:
                return NOT_FOUND
            return _data([{"Disorder": _disorder_block(c, HPODisorderAssociation=_pheno_assoc(c))}
                          for c in codes])
    if product == "rd-associated-genes":
        if parts[1:2] == ["orphacodes"] and len(parts) == 3:
            genes = D.genes(parts[2])
            if not genes:
                return NOT_FOUND
            return _data(_disorder_block(parts[2], DisorderGeneAssociation=genes))
        if parts[1:3] == ["genes", "symbols"] and len(parts) == 4:
            codes = D.by_gene(parts[3])
            if not codes:
                return NOT_FOUND
            return _data([_disorder_block(c, DisorderGeneAssociation=D.genes(c)) for c in codes])
    if product == "rd-classification" and parts[1:2] == ["orphacodes"] and len(parts) == 4 \
            and parts[3] == "hchids":
        cls = D.classifications(parts[2])
        if not cls:
            return NOT_FOUND
        return _data([{"ORPHAcode": _code(parts[2]), "Classification": c["cls"],
                       "parents": [_code(x) for x in c["parents"]],
                       "childs": [_code(x) for x in c["childs"]]} for c in cls])
    if product == "rd-cross-referencing" and parts[1:2] == ["orphacodes"] and len(parts) == 3:
        d = D.disorder(parts[2])
        if not d:
            return NOT_FOUND
        refs = [{"Source": s, "Reference": r, "DisorderMappingRelation": rel}
                for s, r, rel in D.xrefs(parts[2])]
        return _data(_disorder_block(parts[2], ExternalReference=refs,
                                     DisorderType=d["type"], DisorderGroup=d["group"]))
    return None
