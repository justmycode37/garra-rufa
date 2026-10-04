"""Merge the per-paper extractions into one evidence graph and rank candidate solutions.

Nodes are keyed by their normalised id (normalize.py), edges by (subject, predicate,
object); an edge keeps every verified quote from every paper. Edge confidence is a
noisy-OR over the papers that support it, each weighted by its evidence level (LEVEL_W,
x ABSTRACT_W when only the abstract was read); papers reporting a negative / null effect
are counted separately and never add support.

Candidate solutions for the input disease D: every solution node S (drug, therapy,
model_system, biomarker, assay, diagnostic, outcome_measure, resource, method) with an
evidenced edge S -[SOLUTION_PREDICATES]-> X is connected to D through a bridge:
  X is D                                    same disease                        1.0
  X -shares_mechanism_with- D (evidence)    stated by a paper                   0.9
  X and D share a neighbour M               gene (causal 1.0, other 0.5), pathway 0.8,
     (evidence graph or source graph)       process 0.6, cell type 0.5, anatomy 0.4,
                                            phenotypes 0.15 each (max 0.6)
  X is D's ontology parent / child          source graph hierarchy              0.6
  X and D share an ontology parent                                              0.4
  X is a gene / pathway / process / cell type / anatomy / phenotype linked to D
                                            weight of that kind as above
path score = edge confidence x bridge weight (x NEGATIVE_W when most papers on the edge
report no or a negative effect); a candidate's score is the noisy-OR over its paths.
"direct" candidates were already applied to D itself, "transfer" ones come from related
diseases or shared mechanisms. Each becomes an inferred "may_accelerate" edge S -> D.
"""
from collections import defaultdict

from .context import CAUSAL, GENERIC, HIERARCHY
from .extract import SOLUTION_TYPES

LEVEL_W = {"clinical_trial": 0.9, "observational": 0.7, "case_report": 0.5, "animal": 0.5,
           "in_vitro": 0.35, "in_silico": 0.2, "review": 0.2}
ABSTRACT_W = 0.7
NEGATIVE_W = 0.25
SOLUTION_PREDICATES = frozenset({"treats", "rescues", "tested_in", "models", "biomarker_for",
                                 "diagnoses", "measures_outcome_of", "targets",
                                 "applicable_to"})
MECHANISM_PREDICATES = frozenset({"causes", "involves", "has_phenotype",
                                  "shares_mechanism_with"})
KIND_W = {"gene": 0.5, "pathway": 0.8, "process": 0.6, "cell_type": 0.5, "cell": 0.5,
          "anatomy": 0.4}
PHENOTYPE_EACH, PHENOTYPE_MAX = 0.15, 0.6


def noisy_or(ws) -> float:
    p = 1.0
    for w in ws:
        p *= 1 - max(0.0, min(1.0, w))
    return round(1 - p, 3)


class Graph:
    def __init__(self, profile):
        self.profile = profile
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self._nb: dict[str, dict] = {}  # _nbrs cache, valid once all papers are added
        d = profile.disease
        self.node(d["id"], d["label"], "disease", "graph", d.get("xrefs") or [])

    def node(self, nid, label, kind, how, xrefs=(), name=None, paper=None) -> dict:
        n = self.nodes.get(nid)
        if n is None:
            n = self.nodes[nid] = {"id": nid, "label": label, "kind": kind, "how": how,
                                   "names": [], "xrefs": list(xrefs), "papers": [],
                                   "in_source_graph": nid in self.profile.kinds}
        if name and name not in n["names"]:
            n["names"].append(name)
        if paper and paper not in n["papers"]:
            n["papers"].append(paper)
        return n

    def add_paper(self, rec: dict):
        """rec: key, meta, text_source, entities {k: {name, type, norm}}, edges (verified)."""
        pk = rec["key"]
        ids = {}
        for k, e in rec["entities"].items():
            r = e["norm"]
            self.node(r["id"], r["label"], e["type"], r["how"], r.get("xrefs") or [],
                      e["name"], pk)
            ids[k] = r["id"]
        for ed in rec["edges"]:
            s, o = ids[ed["subject"]], ids[ed["object"]]
            if s == o:
                continue
            k = (s, ed["predicate"], o)
            agg = self.edges.setdefault(k, {"from": s, "relation": ed["predicate"], "to": o,
                                            "evidence": []})
            for q in ed["evidence"]:
                agg["evidence"].append({
                    "paper": pk, "pmid": rec["meta"].get("pmid"), "year": rec["meta"].get("year"),
                    "title": rec["meta"].get("title"), "text": rec["text_source"],
                    "level": ed["evidence_level"], "effect": ed["effect"],
                    "organism": ed["organism"], "passage": q["passage"],
                    "section": q.get("section"), "quote": q["quote"]})

    # -- scoring -------------------------------------------------------------------
    def score_edges(self):
        for e in self.edges.values():
            per_paper: dict[str, dict] = {}
            for ev in e["evidence"]:
                w = LEVEL_W.get(ev["level"], 0.2) * (ABSTRACT_W if ev["text"] == "abstract"
                                                     else 1)
                cur = per_paper.get(ev["paper"])
                if cur is None or w > cur["w"]:
                    per_paper[ev["paper"]] = {"w": w, "effect": ev["effect"]}
            sup = [p["w"] * (0.5 if p["effect"] == "mixed" else 1) for p in per_paper.values()
                   if p["effect"] in ("positive", "na", "mixed")]
            neg = [p for p in per_paper.values() if p["effect"] in ("negative", "null")]
            e["papers"] = len(per_paper)
            e["negative_papers"] = len(neg)
            e["confidence"] = noisy_or(sup)
            e["mostly_negative"] = len(neg) > len(sup)
            levels = sorted({ev["level"] for ev in e["evidence"]},
                            key=lambda x: -LEVEL_W.get(x, 0))
            e["level"] = levels[0] if levels else "review"

    # -- neighbourhoods ------------------------------------------------------------
    def _nbrs(self, nid: str) -> dict[str, list[dict]]:
        """neighbour -> [{rel, via, causal}] over mechanism edges of both graphs."""
        if nid in self._nb:
            return self._nb[nid]
        out: dict[str, list[dict]] = defaultdict(list)
        for (s, p, o), e in self.edges.items():
            if p in MECHANISM_PREDICATES and nid in (s, o):
                other = o if s == nid else s
                out[other].append({"rel": p, "via": "evidence", "causal": p == "causes",
                                   "edge": e})
        for other, rels in self.profile.adjacency.get(nid, {}).items():
            out[other].append({"rel": sorted(rels)[0], "via": "source_graph",
                               "causal": any(c in r for r in rels for c in CAUSAL),
                               "hierarchy": any(h in r for r in rels for h in HIERARCHY),
                               "child": any(r.startswith("(inverse)") for r in rels)})
        self._nb[nid] = out
        return out

    def kind(self, nid: str) -> str:
        k = self.nodes[nid]["kind"] if nid in self.nodes else self.profile.kinds.get(nid, "")
        return {"cell": "cell_type"}.get(k, k)

    def label(self, nid: str) -> str:
        return (self.nodes.get(nid) or {}).get("label") or self.profile.labels.get(nid, nid)

    def bridges(self, x: str) -> list[dict]:
        """Ways X connects to the input disease, best first: {weight, why, evidence}."""
        d = self.profile.id
        if x == d:
            return [{"weight": 1.0, "why": "is the input disease", "evidence": []}]
        out = []
        nd, nx_ = self._nbrs(d), self._nbrs(x)
        kx = self.kind(x)
        if kx == "disease":
            for r in nd.get(x, []):
                if r["rel"] == "shares_mechanism_with":
                    out.append({"weight": 0.9, "why": "shares mechanism with input disease",
                                "evidence": _quotes(r["edge"])})
                elif r.get("hierarchy"):
                    out.append({"weight": 0.6, "why": "ontology "
                                + ("subtype" if r["child"] else "parent") + " of input disease",
                                "evidence": [f"source graph: {r['rel']}"]})
            phen = []
            for m in set(nd) & set(nx_):
                km = self.kind(m)
                rd, rx = nd[m], nx_[m]
                ev = [q for r in rd + rx if r["via"] == "evidence" for q in _quotes(r["edge"])]
                if not ev:
                    ev = [f"source graph: {r['rel']}" for r in rd + rx][:2]
                if km == "phenotype":
                    phen.append((m, ev))
                elif km == "gene":
                    causal = any(r["causal"] for r in rd) and any(r["causal"] for r in rx)
                    out.append({"weight": 1.0 if causal else KIND_W["gene"],
                                "why": f"{'causal ' if causal else ''}gene {self.label(m)} "
                                       "in both", "evidence": ev})
                elif km in KIND_W:
                    out.append({"weight": KIND_W[km], "why": f"shared {km} {self.label(m)}",
                                "evidence": ev})
                elif km == "disease" and any(r.get("hierarchy") and not r["child"] for r in rd) \
                        and any(r.get("hierarchy") and not r["child"] for r in rx) \
                        and not m.startswith(GENERIC):
                    out.append({"weight": 0.4, "why": f"sibling under {self.label(m)}",
                                "evidence": [f"source graph: both subclass_of {m}"]})
            if phen:
                out.append({"weight": min(PHENOTYPE_MAX, PHENOTYPE_EACH * len(phen)),
                            "why": f"{len(phen)} shared phenotypes: "
                                   + ", ".join(self.label(m) for m, _ in phen[:6]),
                            "evidence": [q for _, ev in phen[:3] for q in ev[:1]]})
        elif x in nd:  # a mechanism node linked to D
            rs = nd[x]
            if kx == "gene":
                w = 1.0 if any(r["causal"] for r in rs) else KIND_W["gene"]
            elif kx == "phenotype":
                w = 0.3
            else:
                w = KIND_W.get(kx, 0.3)
            ev = [q for r in rs if r["via"] == "evidence" for q in _quotes(r["edge"])] or \
                [f"source graph: {r['rel']}" for r in rs][:2]
            out.append({"weight": w, "why": f"{kx} {self.label(x)} is linked to the input "
                                            "disease", "evidence": ev})
        out.sort(key=lambda b: -b["weight"])
        return out

    def candidates(self) -> list[dict]:
        d = self.profile.id
        by_sol: dict[str, list[dict]] = defaultdict(list)
        for (s, p, o), e in self.edges.items():
            if p not in SOLUTION_PREDICATES or self.kind(s) not in SOLUTION_TYPES:
                continue
            bs = self.bridges(o)
            if not bs:
                continue
            b = bs[0]
            score = e["confidence"] * b["weight"] * (NEGATIVE_W if e["mostly_negative"] else 1)
            by_sol[s].append({"score": round(score, 3), "edge": f"{self.label(s)} {p} "
                              f"{self.label(o)}", "target": o, "predicate": p,
                              "confidence": e["confidence"], "papers": e["papers"],
                              "negative_papers": e["negative_papers"], "level": e["level"],
                              "bridge": b["why"], "bridge_weight": b["weight"],
                              "bridge_evidence": b["evidence"][:3],
                              "evidence": _quotes(e)[:3], "direct": o == d})
        out = []
        for s, paths in by_sol.items():
            paths.sort(key=lambda x: -x["score"])
            score = noisy_or(x["score"] for x in paths)
            if score <= 0:
                continue
            neg_direct = [x for x in paths if x["direct"] and x["negative_papers"]
                          and x["confidence"] == 0]
            out.append({"id": s, "label": self.label(s), "kind": self.kind(s),
                        "score": score,
                        "category": "direct" if any(x["direct"] for x in paths) else "transfer",
                        "tested_without_benefit_in_input": bool(neg_direct),
                        "papers": sorted({ev["paper"] for x in paths
                                          for ev in self.edges_for(s, x)}),
                        "paths": paths[:8]})
        out.sort(key=lambda c: -c["score"])
        return out

    def edges_for(self, s, path) -> list[dict]:
        e = self.edges.get((s, path["predicate"], path["target"]))
        return e["evidence"] if e else []


def _quotes(e: dict) -> list[str]:
    out, seen = [], set()
    for ev in e["evidence"]:
        if ev["paper"] in seen:
            continue
        seen.add(ev["paper"])
        out.append(f"\"{ev['quote']}\" ({ev['paper']}, {ev['passage']})")
    return out
