"""Graph JSON -> the list of literature queries, most important first.

The disease(s) the graph is about (graph["focus"]: the CURIE input, the best name match,
or the top symptom-ranked candidates) are searched on their own and combined with the
entities the graph links to them:

  disease               names (+ MeSH heading when it names the disease)       cap 200
  disease_most_cited    Europe PMC, by citation count                          cap 100
  disease_reviews       PubMed review[pt]                                      cap 50
  disease_preprints     Europe PMC preprints                                   cap 50
  disease_subheading    PubMed "<heading>/genetics|therapy|diagnosis|..."     cap 40 each
  disease+gene          causal / associated genes, most corroborated first     cap 50, <= 10
  disease+drug          approved drugs first, then by trial phase              cap 50, <= 10
  disease+phenotype     specific phenotypes (HPO specificity >= 0.5)           cap 30, <= 10
  variant               variants in the graph with an rsID / protein change   cap 30, <= 20
  gene_variants         LitVar: most published variants of causal genes     cap 150, <= 3
  subtype               subclasses of the disease                              cap 30, <= 5
Further focus diseases (symptom mode) get the core queries and a few gene/drug combos.
symptom_axis (../improvements.py), symptom mode only:
  symptom_treatment     each typed phenotype AND (treatment | therapy | drug)  cap 40, <= 6

Concept names are the label plus exact synonyms (abbreviations such as "MFS" are left out:
as search terms they mostly hit other things). The MeSH heading comes from the mesh
source (info["mesh_heading"]) or a MESH:D* xref looked up at NLM; it is only used as a
MeSH term when its words equal one of the names, since MeSH files many rare diseases
under a broader descriptor.
"""
import re

import improvements
import quality
from sources.base import WORD

from .base import Concept, Provider, Query, Throttle

CAPS = {"disease": 200, "disease_most_cited": 100, "disease_reviews": 50,
        "disease_preprints": 50, "disease_subheading": 40, "disease+gene": 50,
        "disease+drug": 50, "disease+phenotype": 30, "variant": 30, "gene_variants": 150,
        "subtype": 30, "symptom_treatment": 40}
MAX = {"gene": 10, "drug": 10, "phenotype": 10, "variant": 20, "subtype": 5,
       "gene_variants": 3, "symptom_treatment": 6}
MAX_SECONDARY = {"gene": 3, "drug": 3, "phenotype": 0, "subtype": 0}
SUBHEADINGS = ["genetics", "therapy", "diagnosis", "physiopathology", "drug therapy",
               "surgery"]
MESH_LOOKUP = "https://id.nlm.nih.gov/mesh/lookup/label"
CAUSAL = ("caused_by", "causes", "material_basis", "disease_causing")
APPROVED = ("approved_drug", "indicated_for", "treated_by", "indication")


class MeshLookup(Provider):
    """MeSH descriptor id -> heading (NLM lookup service, cached)."""
    name = "mesh"
    throttle = Throttle(0.1)

    def heading(self, did: str) -> str | None:
        try:
            r = self.fetch_json(MESH_LOOKUP, {"resource": did})
            return r[0] if r else None
        except Exception as e:
            self.fail(f"lookup {did}", e)
            return None


def _words(s: str) -> frozenset[str]:
    return frozenset(WORD.findall(s.lower().replace("'s", "s")))


ABBREVIATION = re.compile(r"^[A-Z]{2,6}\d{0,2}[A-Z]?$")  # MFS, MFS1, CDG1A (not PMM2-CDG)


def _names(n: dict, extra: list[str] = ()) -> list[str]:
    info = n.get("info") or {}
    out, seen = [], set()
    for x in [n["label"], *extra, *(info.get("synonyms") or [])]:
        x = (x or "").strip()
        k = " ".join(WORD.findall(x.lower()))
        # skip abbreviations ("MFS"), placeholders ("ORPHA:558") and duplicates
        if not k or k in seen or len(x) < 4 or ABBREVIATION.match(x) or ":" in x:
            continue
        seen.add(k)
        out.append(x)
    return out[:8]


def _xref(n: dict, prefix: str) -> list[str]:
    return [x.split(":", 1)[1] for x in [n["id"], *n.get("xrefs", [])]
            if x.split(":", 1)[0].upper() == prefix.upper()]


def concept(n: dict, mesh: MeshLookup, support: int = 0, extra: list[str] = ()) -> Concept:
    """`extra`: more names, e.g. the user's free-text input the disease was found by."""
    info = n.get("info") or {}
    kind = n["kind"]
    c = Concept(n["id"], n["label"], kind, _names(n, extra), support=support)
    if kind == "gene":
        sym = n["label"] if re.fullmatch(r"[A-Za-z0-9-]+", n["label"]) else \
            next(iter(_xref(n, "SYMBOL")), None)
        c.symbol = sym
        c.ncbigene = next(iter(_xref(n, "NCBIGene")), None)
        c.names = [x for x in [*([info["full_name"]] if info.get("full_name") else []),
                               *c.names] if x != sym][:3]
    elif kind == "variant":
        c.rsid = next((x for x in _xref(n, "dbSNP") if x.startswith("rs")), None)
        if n["id"].startswith("dbSNP:"):
            c.rsid = n["id"].split(":", 1)[1]
        m = re.search(r"\(([A-Za-z0-9-]+)\):", n["label"])
        c.gene = (info.get("genes") or [None])[0] or (m.group(1) if m else None)
        m = re.search(r"\((p\.[^)]+)\)", n["label"])
        c.hgvs = m.group(1) if m else None
    else:
        # (heading, descriptor id) candidates; the first whose words equal a name wins,
        # else the first one is kept as a broader heading (not used as a MeSH term)
        names = {_words(x) for x in c.names}
        dids = [d for d in _xref(n, "MESH") if d[:1] in "DC"]
        cands = [(info["mesh_heading"], None)] if info.get("mesh_heading") else []
        for did in dids[:3]:
            if not any(_words(h) in names for h, _ in cands):
                h = mesh.heading(did)
                if h:
                    cands.append((h, did))
        exact = [x for x in cands if _words(x[0]) in names]
        if exact or cands:
            c.mesh, c.mesh_id = (exact or cands)[0]
            c.mesh_exact = bool(exact)
            c.mesh_id = c.mesh_id or next((d for h, d in cands if h == c.mesh and d), None) \
                or (dids[0] if len(dids) == 1 else None)
        if c.mesh and not c.names:
            c.names = [c.mesh]
    return c


def _neighbours(graph: dict, nid: str) -> dict[str, dict]:
    """Neighbour id -> {relations, sources} over all edges touching nid (both directions;
    an inverse edge gets an "(inverse) " relation prefix)."""
    out: dict[str, dict] = {}
    for e in graph["edges"]:
        if nid not in (e["from"], e["to"]) or e["from"] == e["to"]:
            continue
        other = e["to"] if e["from"] == nid else e["from"]
        rel = e["relation"] if e["from"] == nid else "(inverse) " + e["relation"]
        d = out.setdefault(other, {"relations": set(), "sources": set()})
        d["relations"].add(rel)
        d["sources"].add(e["source"])
    return out


def _drug_rank(rels: set[str]) -> int:
    best = 9
    for r in rels:
        r = r.removeprefix("(inverse) ")
        if any(a in r for a in APPROVED):
            best = min(best, 0)
        elif r.startswith("trial_drug_phase_"):
            best = min(best, 5 - int(r.removeprefix("trial_drug_phase_")[0]))
    return best


def plan(graph: dict, mesh: MeshLookup) -> list[Query]:
    nodes = {n["id"]: n for n in graph["nodes"]}
    focus = list(dict.fromkeys(f for f in graph.get("focus") or [] if f in nodes)) or \
        ([graph["start"]] if graph["start"] in nodes else [])
    core: list[Query] = []
    extra: list[Query] = []
    combos: list[list[Query]] = []
    subtypes: list[Query] = []
    gene_vars: list[Query] = []
    start = nodes.get(graph["start"])
    typed = [start["label"]] if start and graph["start"].startswith("term:") else []
    for fi, fid in enumerate(focus):
        dis = concept(nodes[fid], mesh, extra=typed if fi == 0 else [])
        if not dis.names and not dis.mesh:
            continue
        lim = MAX if fi == 0 else MAX_SECONDARY

        def q(rel, other=None, qualifier=None, _d=dis):
            ids = "|".join(x for x in (_d.id, other.id if other else None, qualifier) if x)
            return Query(f"{rel}:{ids}", rel, _d, other, CAPS[rel], qualifier)
        core.append(q("disease"))
        extra += [q("disease_most_cited"), q("disease_reviews")]
        if fi == 0:
            extra.append(q("disease_preprints"))
            # subheadings exist for descriptors (D...), not supplementary concepts (C...)
            if dis.mesh and dis.mesh_exact and not (dis.mesh_id or "").startswith("C"):
                extra += [q("disease_subheading", qualifier=s) for s in SUBHEADINGS]
        nb = _neighbours(graph, fid)
        genes, drugs, phens, subs = [], [], [], []
        for oid, d in nb.items():
            n = nodes.get(oid)
            if not n or oid == graph["start"]:
                continue
            support = len(d["sources"])
            rels = d["relations"]
            if n["kind"] == "gene" and "(" not in n["label"]:  # "(Mus musculus)" orthologs
                causal = any(c in r for r in rels for c in CAUSAL)
                genes.append(((not causal, -support, n["label"]), n, support))
            elif n["kind"] == "drug":
                drugs.append(((_drug_rank(rels), -support, n["label"]), n, support))
            elif n["kind"] == "phenotype":
                spec = quality.specificity(oid, set(n.get("xrefs") or []), n["label"], "phenotype")
                if spec >= 0.5:
                    phens.append(((-support, -spec, n["label"]), n, support))
            elif n["kind"] == "disease" and ("has_subclass" in rels
                                              or "(inverse) subclass_of" in rels):
                subs.append(((-support, n["label"]), n, support))
        groups = []
        for rel, items, kind in (("disease+gene", genes, "gene"), ("disease+drug", drugs, "drug"),
                                 ("disease+phenotype", phens, "phenotype")):
            items.sort(key=lambda t: t[0])
            groups.append([q(rel, concept(n, mesh, s)) for _, n, s in items[:lim[kind]]])
        combos += groups
        if fi == 0:  # variants of causal genes only (associated genes can be differentials)
            causal = [n for key, n, _ in genes if not key[0]][:MAX["gene_variants"]]
            gene_vars += [q("gene_variants", concept(n, mesh)) for n in causal]
        subs.sort(key=lambda t: t[0])
        subtypes += [q("subtype", concept(n, mesh, s)) for _, n, s in subs[:lim["subtype"]]]

    # variants anywhere in the graph (they hang off genes, not the disease): pathogenic first
    variants = []
    if focus:
        main = concept(nodes[focus[0]], mesh)
        for n in graph["nodes"]:
            if n["kind"] != "variant":
                continue
            rels = {r for e in graph["edges"] if n["id"] in (e["from"], e["to"])
                    for r in [e["relation"]]}
            path = any("pathogenic" in r and "conflict" not in r for r in rels)
            c = concept(n, mesh)
            if c.rsid or (c.gene and c.hgvs):
                variants.append(((not path, not c.rsid, n["label"]), c))
        variants.sort(key=lambda t: t[0])
        variants = [Query(f"variant:{c.id}", "variant", main, c, CAPS["variant"])
                    for _, c in variants[:MAX["variant"]]]

    # combos round-robin over genes / drugs / phenotypes, best of each first
    mixed = []
    for i in range(max((len(g) for g in combos), default=0)):
        mixed += [g[i] for g in combos if i < len(g)]
    symptoms = symptom_queries(graph, nodes, mesh) if improvements.on("symptom_axis") else []
    return core + extra + symptoms + mixed + variants + gene_vars + subtypes


def symptom_queries(graph: dict, nodes: dict, mesh: MeshLookup) -> list[Query]:
    """symptom_axis: for a symptom-mode graph (query.mode "candidates"), one query per
    typed phenotype (at most MAX["symptom_treatment"]): "<symptom> AND (treatment OR
    therapy OR drug)", so treatments of the symptom itself (acquired HO, cataplexy, ...)
    reach the evidence graph whatever the disease."""
    q = graph.get("query") or {}
    if q.get("mode") != "candidates":
        return []
    out, seen = [], set()
    for p in q.get("parts") or []:
        if p.get("kind") != "phenotype" or not p.get("id") or p["id"] in seen:
            continue
        seen.add(p["id"])
        n = nodes.get(p["id"]) or {"id": p["id"], "label": p.get("label") or p["text"],
                                   "kind": "phenotype", "xrefs": [], "info": {}}
        c = concept({**n, "kind": "phenotype"}, mesh)
        if not c.names:
            c.names = [p.get("label") or p["text"]]
        out.append(Query(f"symptom_treatment:{p['id']}", "symptom_treatment", c, None,
                         CAPS["symptom_treatment"]))
    return out[:MAX["symptom_treatment"]]
