"""NLM MeSH RDF API (https://id.nlm.nih.gov/mesh) from mesh.sqlite.

  /lookup/descriptor?label=..&match=exact    [{"resource", "label"}]
  /lookup/label?resource=D000001             ["label"]
  /sparql?query=..                           the two query shapes sources/mesh.py sends:
      neighbours (self / broaderDescriptor / seeAlso with tree numbers) and scopeNote
"""

from __future__ import annotations

import re

from garra.local import mesh as M
from garra.local.router import Reply, Req, register

HOST = "id.nlm.nih.gov"
URI = "http://id.nlm.nih.gov/mesh/"
_ID = re.compile(r"mesh:([DC]\d+)")


@register(HOST, "/mesh/lookup/descriptor", "mesh")
def lookup_descriptor(req: Req) -> Reply | None:
    label = req.get("label")
    if not label or (req.get("match") or "exact") != "exact":
        return None
    uis = [u for u in M.by_name(label, "D")
           if (M.name(u) or "").lower() == label.strip().lower()] or M.by_name(label, "D")
    n = int(req.get("limit") or 10)
    return Reply([{"resource": URI + u, "label": M.name(u)} for u in uis[:n]])


@register(HOST, "/mesh/lookup/label", "mesh")
def lookup_label(req: Req) -> Reply | None:
    ui = (req.get("resource") or "").rsplit("/", 1)[-1]
    if not ui:
        return None
    nm = M.name(ui)
    return Reply([nm] if nm else [])


def _b(**kw) -> dict:
    return {k: {"type": "uri" if k == "d" else "literal", "value": v} for k, v in kw.items()}


@register(HOST, "/mesh/sparql", "mesh")
def sparql(req: Req) -> Reply | None:
    q = req.get("query") or ""
    m = _ID.search(q)
    if not m:
        return None
    ui = m[1]
    if "scopeNote" in q:
        r = M.record(ui)
        rows = [_b(note=r["note"])] if r and r["note"] else []
        return Reply({"head": {"vars": ["note"]}, "results": {"bindings": rows}})
    if "broaderDescriptor" not in q:
        return None
    if not M.record(ui):
        return Reply({"head": {"vars": []}, "results": {"bindings": []}})
    pairs = [("self", ui)] + [("subclass_of", x) for x in M.broader(ui)] \
        + [("has_subclass", x) for x in M.narrower(ui)] \
        + [("see_also", x) for x in M.see_also(ui)]
    rows = []
    for rel, d in pairs:
        label = M.name(d)
        if not label:
            continue
        trees = M.trees(d)
        for tn in trees or [None]:
            b = _b(rel=rel, d=URI + d, label=label)
            if tn:
                b["tn"] = {"type": "literal", "value": tn}
            rows.append(b)
    return Reply({"head": {"vars": ["rel", "d", "label", "tn"]}, "results": {"bindings": rows}})
