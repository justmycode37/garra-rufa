"""The input disease as both LLM passes see it, from the graph JSON (../main.py) and the
papers JSON (../literature/main.py).

Profile    focus disease (id, names, xrefs, description), its causal / associated genes,
           most specific phenotypes, pathways, known drugs, the related diseases the
           graph links to it (ontology parents / children / Orphanet groups / further
           symptom-mode candidates), rendered as a compact text block (text()).
index      every graph node by normalised name (label, synonyms, aliases, the literature
           concepts' names) -> (id, label, kind), so normalize.py maps LLM entity names to
           the ids the graph already uses before asking any ontology service.
adjacency  undirected neighbours per node over the graph's biomedical edges (no xrefs,
           organisations, trials), used by build.py to find shared mechanisms.
symptoms   symptom_axis (../improvements.py): the phenotypes the user typed in symptom mode
           (graph["query"]), listed in text() and bridged with SYMPTOM_W by build.py.
"""
import re
from dataclasses import dataclass, field

import improvements
from sources.base import WORD

# relations that say nothing about biology (identity, organisations, ...)
NON_BIO = ("xref", "matches", "organisation", "expert_", "network", "registry", "biobank",
           "research_project", "clinical_trial", "sponsored", "therapeutic_area", "consortium",
           "investigator", "collaborator", "ern_", "inheritance")
CAUSAL = ("caused_by", "causes", "material_basis", "disease_causing")
HIERARCHY = ("subclass_of", "has_subclass", "parent_child")
MAX_ASSOCIATED_GENES = 6
# disease classes too broad to say anything as "related disease"
GENERIC = ("OTAR:", "DOID:0050737", "DOID:4", "MONDO:0000001", "MONDO:0700096",
           "MONDO:0003847", "ORPHA:68367")


def key(name: str) -> str:
    """Normalised lookup key of a name: lower-case alphanumeric words."""
    return " ".join(WORD.findall((name or "").lower().replace("'s", "s")))


BAD_CHARS = re.compile(r"[\\\"“”«»`\x00-\x1f\x7f]")


def sanitize_terms(names, min_len: int = 4) -> list[str]:
    """query_hygiene: names safe to put into a quoted Europe PMC / ClinicalTrials.gov term:
    backslashes, quotes and control characters removed ("Alstrom\\" -> "Alstrom"),
    whitespace collapsed, stray punctuation trimmed; names with a ";" (several items) or
    shorter than min_len letters/digits dropped; duplicates (by key) dropped."""
    out, seen = [], set()
    for n in names or []:
        t = " ".join(BAD_CHARS.sub(" ", str(n or "")).split()).strip(" ,.:;-/'")
        k = key(t)
        if not t or ";" in t or len(k.replace(" ", "")) < min_len or k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def is_placeholder(n: dict) -> bool:
    return n["label"] == n["id"] or bool(re.fullmatch(r"[A-Z.]+:\S+", n["label"]))


def node_names(n: dict, concept: dict | None = None) -> list[str]:
    info = n.get("info") or {}
    names = [n["label"], *(concept or {}).get("names", []), *(info.get("synonyms") or []),
             *(info.get("aliases") or [])]
    if info.get("full_name"):
        names.append(info["full_name"])
    out, seen = [], set()
    for x in names:
        k = key(x)
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


@dataclass
class Profile:
    disease: dict  # id, label, names, xrefs, description
    genes: list[dict] = field(default_factory=list)  # id, label, causal
    phenotypes: list[dict] = field(default_factory=list)
    pathways: list[dict] = field(default_factory=list)
    drugs: list[dict] = field(default_factory=list)
    related: list[dict] = field(default_factory=list)  # id, label, relation
    index: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    adjacency: dict[str, dict[str, set[str]]] = field(default_factory=dict)  # a -> b -> rels
    kinds: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    # symptom_axis: the phenotypes the user typed (symptom mode), {id, label}
    symptoms: list[dict] = field(default_factory=list)
    # input_identity: graph diseases that are the input's unqualified parent ("Rett
    # syndrome" for "Atypical Rett syndrome"), {id, label, names, xrefs}; registries.py
    # also queries trials / known drugs with them
    identity: list[dict] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.disease["id"]

    def text(self) -> str:
        d = self.disease
        lines = [f"INPUT DISEASE: {d['label']} ({d['id']})"]
        if d.get("names"):
            lines.append("  synonyms: " + "; ".join(d["names"][1:12]))
        if d.get("xrefs"):
            lines.append("  ids: " + ", ".join(d["xrefs"][:12]))
        if d.get("description"):
            lines.append("  description: " + d["description"][:900])

        def block(title, items, fmt):
            if items:
                lines.append(f"{title}: " + "; ".join(fmt(x) for x in items))
        block("GENES", self.genes, lambda g: g["label"] + (" (causal)" if g["causal"]
                                                           else " (associated)"))
        block("PATHWAYS", self.pathways, lambda x: x["label"])
        block("KEY PHENOTYPES", self.phenotypes, lambda x: x["label"])
        block("KNOWN DRUGS / TRIALS", self.drugs, lambda x: f"{x['label']} [{x['relation']}]")
        block("RELATED DISEASES (from the knowledge graph)", self.related,
              lambda x: f"{x['label']} [{x['relation']}]")
        block("SYMPTOMS THE USER SEARCHED BY (treatments of these symptoms are solutions "
              "too)", self.symptoms, lambda x: x["label"])
        return "\n".join(lines)


def typed_symptoms(graph: dict) -> list[dict]:
    """{id, label, names} of the phenotypes a symptom-mode query was read as
    (graph["query"]); names: the HPO label and the text the user typed."""
    q = graph.get("query") or {}
    if q.get("mode") != "candidates":
        return []
    out = []
    for p in q.get("parts") or []:
        if p.get("kind") == "phenotype" and p.get("id") and \
                all(x["id"] != p["id"] for x in out):
            label = p.get("label") or p.get("text") or p["id"]
            out.append({"id": p["id"], "label": label,
                        "names": list(dict.fromkeys(x for x in (label, p.get("text")) if x))})
    return out


def parents(graph: dict, prof: Profile, concepts: dict | None = None,
            limit: int = 3) -> list[dict]:
    """input_identity: graph disease nodes whose label is the input label / name without
    its qualifiers (build.qualified_match "parent"), most connected first."""
    from .build import qualified_match  # build imports this module
    d = prof.disease
    names = [d["label"], *(d.get("names") or [])]
    deg = {i: len(nb) for i, nb in prof.adjacency.items()}
    out = []
    for n in graph["nodes"]:
        if n["kind"] != "disease" or n["id"] == d["id"] or is_placeholder(n) or \
                n["id"].startswith(GENERIC):
            continue
        if qualified_match(n["label"], names) == "parent":
            out.append({"id": n["id"], "label": n["label"],
                        "names": node_names(n, (concepts or {}).get(n["id"])),
                        "xrefs": [x for x in n.get("xrefs") or [] if not x.startswith("UMLS")]})
    out.sort(key=lambda x: (-deg.get(x["id"], 0), x["id"]))
    return out[:limit]


def build_profile(graph: dict, papers: dict) -> Profile:
    nodes = {n["id"]: n for n in graph["nodes"]}
    concepts = papers.get("concepts") or {}
    focus = [f["id"] for f in papers.get("focus") or []] or list(graph.get("focus") or []) \
        or [graph["start"]]
    focus = [f for f in focus if f in nodes]
    fid = focus[0]
    fn = nodes[fid]
    info = fn.get("info") or {}
    desc = info.get("description") or next(iter((info.get("descriptions") or {}).values()), "")
    prof = Profile({"id": fid, "label": fn["label"], "names": node_names(fn, concepts.get(fid)),
                    "xrefs": [x for x in fn.get("xrefs") or [] if not x.startswith("UMLS")],
                    "description": desc})

    # adjacency over biomedical edges (deduplicated; graph JSON repeats edges per source)
    adj: dict[str, dict[str, set[str]]] = {}
    for e in graph["edges"]:
        a, b, rel = e["from"], e["to"], e["relation"]
        if a == b or any(x in rel for x in NON_BIO):
            continue
        adj.setdefault(a, {}).setdefault(b, set()).add(rel)
        adj.setdefault(b, {}).setdefault(a, set()).add("(inverse) " + rel)
    prof.adjacency = adj
    prof.kinds = {i: n["kind"] for i, n in nodes.items()}
    prof.labels = {i: n["label"] for i, n in nodes.items()}

    # neighbours of the focus disease by kind
    import quality  # local HPO files; loaded lazily (slow on first use)
    phens = []
    for oid, rels in adj.get(fid, {}).items():
        n = nodes.get(oid)
        if not n or is_placeholder(n):
            continue
        r = sorted(rels)[0]
        if n["kind"] == "gene" and "(" not in n["label"]:
            prof.genes.append({"id": oid, "label": n["label"],
                               "causal": any(c in x for x in rels for c in CAUSAL)})
        elif n["kind"] == "phenotype" and "has_phenotype" in rels:
            try:
                spec = quality.specificity(oid, set(n.get("xrefs") or []), n["label"],
                                           "phenotype")
            except Exception:
                spec = 0.5
            phens.append((-spec, n["label"], {"id": oid, "label": n["label"]}))
        elif n["kind"] == "pathway":
            prof.pathways.append({"id": oid, "label": n["label"]})
        elif n["kind"] == "drug":
            prof.drugs.append({"id": oid, "label": n["label"], "relation": r})
        elif n["kind"] == "disease":
            prof.related.append({"id": oid, "label": n["label"], "relation": r})
    for other in focus[1:]:  # symptom mode: the further candidates
        prof.related.append({"id": other, "label": nodes[other]["label"],
                             "relation": "symptom_candidate"})
    prof.genes.sort(key=lambda g: (not g["causal"], g["label"]))
    prof.genes = [g for g in prof.genes if g["causal"]] +         [g for g in prof.genes if not g["causal"]][:MAX_ASSOCIATED_GENES]
    prof.phenotypes = [x for _, _, x in sorted(phens, key=lambda t: t[:2])][:25]
    seen = set()
    prof.related = [r for r in prof.related if not r["id"].startswith(GENERIC)
                    and key(r["label"]) not in seen and not seen.add(key(r["label"]))][:25]
    if improvements.on("symptom_axis"):
        prof.symptoms = typed_symptoms(graph)
    if improvements.on("input_identity"):
        prof.identity = parents(graph, prof, concepts)
        have = {key(x["label"]) for x in prof.drugs}
        for p in prof.identity:  # the parent's known drugs / trial drugs are the input's
            for oid, rels in adj.get(p["id"], {}).items():
                n = nodes.get(oid)
                if n and n["kind"] == "drug" and not is_placeholder(n) and \
                        key(n["label"]) not in have:
                    have.add(key(n["label"]))
                    prof.drugs.append({"id": oid, "label": n["label"],
                                       "relation": f"{sorted(rels)[0]} (of {p['label']})"})

    # name index over all graph nodes
    for n in graph["nodes"]:
        if is_placeholder(n) or n["kind"] in ("term", "query"):
            continue
        for name in node_names(n, concepts.get(n["id"])):
            k = key(name)
            if len(k) < 3:
                continue
            ent = (n["id"], n["label"], n["kind"])
            if ent not in prof.index.setdefault(k, []):
                prof.index[k].append(ent)
    return prof
