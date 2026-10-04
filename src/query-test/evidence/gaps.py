"""Gap finding: "mechanism M is established in the disease (or its models), intervention
Y acts on M elsewhere, but nobody has tested Y in the disease".

For every transfer candidate that is a drug or therapy (build.Graph.candidates: never
tested in the disease or its models), each path is matched to a mechanism node M from
build.Graph.mechanisms():
  direct    Y -[rescues|targets|treats|...]-> M
  via       Y -> disease / model X, and X shares M with the input disease (evidence or
            source graph neighbours of both)
The gap is then checked against what the pipeline did not read:
  Europe PMC  papers naming Y (any of its names) AND the disease in title/abstract
  trials      ClinicalTrials.gov trials of Y for the disease
status: "untested" (no paper, no trial), "barely_tested" (<= FEW papers, no trial; the
papers are listed to check), "tested" (more papers or a trial: not a gap, kept in the JSON
so the reader sees why). Untested / barely tested gaps go to one LLM call (review()) that
rates how plausible the transfer is (0-3) and names the main caveat; gaps are ranked by
status, plausibility and score (path score x mechanism confidence).

gap_check (../improvements.py): gaps of interventions with one canonical name (build.
canon_key: "momelotinib" / "MOMELOTINIB DIHYDROCHLORIDE") are one gap; a gap is "tested"
(with a `reason`) when a direct candidate of the input has the same modality (MODALITIES:
gene therapy, antisense, antibody, ...) on the same target (same_in_graph); otherwise the
Europe PMC check also runs with the core of the name (core_queries: "activin A" +
antibody, the construct's head + product) and the larger count decides.
"""
import re

import improvements

from .build import SOLUTION_PREDICATES, canon_key
from .context import key

FEW = 3
REVIEW_SYSTEM = """You review research gaps for a rare-disease team. Each gap says: a
mechanism is established in the input disease, an intervention acts on that mechanism in
another setting (quotes given), and no paper or trial tests the intervention in the input
disease. Rate how plausible and worthwhile testing it in the input disease is:
3 = strong rationale, mechanism central to the disease, intervention available / safe enough
2 = reasonable hypothesis with clear caveats
1 = weak (mechanism peripheral, intervention unsuitable, e.g. wrong tissue or toxic)
0 = nonsense (mapping error, the "intervention" is not an intervention, circular)
Use the quotes; general pharmacology knowledge is allowed for caveats (blood-brain
barrier, toxicity, approval status) but do not invent results.
Answer with one JSON object: {"gaps": [{"i": <index>, "plausibility": 0-3,
"rationale": "one sentence", "caveat": "one sentence", "experiment": "the first
experiment to run, one sentence"}]}"""


def find(g, candidates: list[dict], reg, profile, limit: int = 40) -> list[dict]:
    mechs = {m["id"]: m for m in g.mechanisms(limit=60)}
    if not mechs:
        return []
    d_nbrs = set(g.input_nbrs())
    gaps: dict[tuple, dict] = {}
    for c in candidates:
        if c["category"] != "transfer" or c["kind"] not in ("drug", "therapy"):
            continue
        for x in c["paths"]:
            if x["predicate"] not in SOLUTION_PREDICATES or x["confidence"] <= 0:
                continue
            t = x["target"]
            if t in mechs:
                hits = [(t, "acts on it directly", None)]
            else:
                shared = (set(g._nbrs(t)) & d_nbrs) & set(mechs)
                hits = [(m, f"via {g.label(t)}", t) for m in shared]
            for m, how, other in hits:
                k = (c["id"], m)
                score = round(x["score"] * mechs[m]["confidence"], 3)
                if k in gaps and gaps[k]["score"] >= score:
                    continue
                gaps[k] = {"intervention": c["id"], "intervention_label": c["label"],
                           "kind": c["kind"], "mechanism": m,
                           "mechanism_label": mechs[m]["label"],
                           "mechanism_kind": mechs[m]["kind"],
                           "mechanism_evidence": mechs[m]["evidence"][:2],
                           "mechanism_papers": mechs[m]["papers"][:6],
                           "how": how, "other_setting": other and g.label(other),
                           "intervention_edge": x["edge"], "level": x["level"],
                           "intervention_evidence": x["evidence"][:2],
                           "found_by": x.get("found_by") or [], "score": score}
    # one gap per intervention (its best mechanism), best first; gap_check: per canonical
    # intervention (momelotinib / momelotinib dihydrochloride are one gap)
    check = improvements.on("gap_check")
    best: dict[str, dict] = {}
    for gp in sorted(gaps.values(), key=lambda x: -x["score"]):
        k = canon_key(gp["intervention_label"], gp["kind"]) if check else gp["intervention"]
        if k in best and check and gp["intervention"] != best[k]["intervention"]:
            best[k].setdefault("merged_interventions", []).append(gp["intervention"])
        best.setdefault(k, gp)
    out = sorted(best.values(), key=lambda x: -x["score"])[:limit]
    dnames = [n for n in profile.disease.get("names") or [profile.disease["label"]]]
    direct = [c for c in candidates if c["category"] == "direct" and c["kind"] in
              ("drug", "therapy")] if check else []
    causal = {x["id"] for x in profile.genes if x.get("causal")}
    for gp in out:
        node = g.nodes.get(gp["intervention"]) or {}
        names = [gp["intervention_label"], *(node.get("names") or [])]
        for other in gp.get("merged_interventions") or []:
            names += [g.label(other), *((g.nodes.get(other) or {}).get("names") or [])]
        lit = reg.cooccurrence(names, dnames)
        trials = reg.trials_of(names, profile)
        gp["literature"] = lit
        gp["trials"] = [{"nct": t["nct"], "title": t["title"], "status": t["status"]}
                        for t in trials]
        n = lit["count"]
        same = core = ""
        if check:
            same = same_in_graph(gp, direct, causal, g)
            for q in [] if same else core_queries(gp, causal):
                r = reg.cooccurrence(q["names"], dnames, also=q["also"])
                gp.setdefault("literature_core", []).append(
                    {"names": q["names"], "also": q["also"], "count": r["count"],
                     "papers": r["papers"], "query": r["query"]})
                if r["count"] is not None and (n is None or r["count"] > n):
                    n = r["count"]
                    core = (f"{n} papers name the core intervention "
                            f"({' / '.join(q['names'][:2])}"
                            + (f" + {q['also'][0]}" if q["also"] else "") + ") and the disease")
        if trials:
            gp["status"], reason = "tested", f"{len(trials)} registered trial(s) in the disease"
        elif same:
            gp["status"], reason = "tested", same
        elif n is not None and n > FEW:
            gp["status"], reason = "tested", core or f"{n} papers name it and the disease"
        elif n == 0:
            gp["status"], reason = "untested", ""
        else:  # a few papers, or the count failed
            gp["status"], reason = ("barely_tested" if n else "unknown"), core
        if check:
            gp["reason"] = reason
    return out


# -- gap_check: modality / target matching and core names --------------------------------
MODALITIES = {
    "gene therapy": r"\b(aav\w*|adeno associated|gene therap\w*|gene transfer|gene replacement|"
                    r"lentivir\w*|viral vector\w*)\b",
    "gene editing": r"\b(crispr|cas9|base edit\w*|prime edit\w*|gene edit\w*)\b",
    "antisense": r"\b(antisense|asos?|oligonucleotides?|splice switching|morpholinos?|"
                 r"\w+rsen)\b",
    "rna interference": r"\b(sirna|rnai|shrna|\w+siran)\b",
    "antibody": r"\b(antibod\w*|\w+mab|monoclonal|nanobod\w*)\b",
    "enzyme replacement": r"\b(enzyme replacement|ert|\w+ase alfa|\w+ase beta)\b",
    "chaperone": r"\b(chaperon\w*)\b",
    "substrate reduction": r"\b(substrate reduction)\b",
    "cell therapy": r"\b(stem cells?|cell therap\w*|transplant\w*)\b",
    "mrna therapy": r"\b(mrna)\b",
}
# gene-level modalities: in a monogenic disease every one of them acts on its causal gene
GENE_LEVEL = {"gene therapy", "gene editing", "antisense", "enzyme replacement", "mrna therapy",
              "rna interference"}
MODALITY_TERMS = {"gene therapy": ["gene therapy", "adeno-associated", "gene transfer"],
                  "gene editing": ["gene editing", "CRISPR"],
                  "antisense": ["antisense", "oligonucleotide"],
                  "rna interference": ["siRNA", "RNA interference"],
                  "antibody": ["antibody", "antibodies"],
                  "enzyme replacement": ["enzyme replacement"],
                  "chaperone": ["chaperone"], "substrate reduction": ["substrate reduction"],
                  "cell therapy": ["stem cell", "cell therapy", "transplantation"],
                  "mrna therapy": ["mRNA therapy"]}
ROUTE = re.compile(r"^.*?\b(?:injection|administration|delivery|infusion|instillation|"
                   r"dosing|transfer|treatment) (?:of|with)\s+", re.I)
CONSTRUCT = re.compile(r"\s+(?:encoding|expressing|carrying|containing|targeting|directed "
                       r"against|against|that targets|for)\s+", re.I)
FILLER = re.compile(r"\b(?:recombinant|human|neutrali[sz]ing|monoclonal|humani[sz]ed|fully|"
                    r"vectors?|viral|virus|serotype \w+|mediated|based|systemic|intravenous|"
                    r"intrathecal|intracerebroventricular|subcutaneous|oral|high dose|low dose|"
                    r"therapy|therapeutic|treatment|inhibitors?|agents?|drugs?)\b", re.I)
ROUTE_WORDS = frozenset({"injection", "infusion", "delivery", "administration", "encoding",
                         "expressing", "loaded", "derived", "using", "cells", "carrying",
                         "cistern", "cerebellomedullary", "intracisternal", "intravitreal"})
HEAD_FILLER = re.compile(r"\b(?:recombinant|human|vectors?|viral|virus|serotype \w+|mediated|"
                         r"systemic|intravenous|intrathecal|intracerebroventricular|"
                         r"subcutaneous|oral|high dose|low dose)\b", re.I)


def modalities(text: str) -> set[str]:
    k = key(text)
    return {m for m, rx in MODALITIES.items() if re.search(rx, k)}


def _strip(text: str) -> str:
    """key() of a name without modality and filler words ("activin A neutralizing
    monoclonal antibody" -> "activin a")."""
    k = key(text)
    for rx in MODALITIES.values():
        k = re.sub(rx, " ", k)
    return " ".join(key(FILLER.sub(" ", k)).split())


def _target_words(label: str) -> set[str]:
    """Specific words (>= 5 letters) of what an intervention acts on: the construct's
    product, else its head, without modality / filler / route words ("activin" of
    "activin A neutralizing monoclonal antibody", "sphingomyelinase" of "... AAV9
    encoding human acid sphingomyelinase")."""
    head, product = core_names(label)
    return {w for w in _strip(product or head).split() if len(w) >= 5 and w not in ROUTE_WORDS}


def same_in_graph(gp: dict, direct: list[dict], causal: set[str], g) -> str:
    """'graph: ...' when a direct candidate of the input (tested in it / its models, or a
    registered trial) has the gap intervention's modality on the same target: its edges
    reach the gap's mechanism, a gene-level modality and the mechanism is a causal gene of
    the input, or its names / quotes mention the intervention's specific target word."""
    mods = modalities(gp["intervention_label"])
    if not mods:
        return ""
    words = _target_words(gp["intervention_label"])
    for c in direct:
        if c["id"] == gp["intervention"]:
            continue
        node = g.nodes.get(c["id"]) or {}
        both = mods & modalities(" ".join([c["label"], *(node.get("names") or [])]))
        if not both:
            continue
        mod = sorted(both)[0]
        text = key(" ".join([c["label"], *(node.get("names") or []),
                             *(x["edge"] for x in c["paths"]),
                             *(q for x in c["paths"] for q in x["evidence"])]))
        if any(x["target"] == gp["mechanism"] for x in c["paths"]):
            why = f"acts on {gp['mechanism_label']}"
        elif gp["mechanism"] in causal and both & GENE_LEVEL:
            why = f"{gp['mechanism_label']} is the causal gene"
        elif words and any(w in text.split() for w in words):
            why = "names " + ", ".join(sorted(w for w in words if w in text.split()))
        else:
            continue
        return (f"graph: same modality ({mod}) as the direct candidate {c['label']} "
                f"({why})")
    return ""


def core_names(label: str) -> tuple[str, str]:
    """(head, product) of a construct name: route / delivery prefix and filler words
    removed, split at "encoding" / "targeting" / ... ("cerebellomedullary-cistern injection
    of adeno-associated viral vector serotype 9 encoding human acid sphingomyelinase" ->
    ("adeno-associated", "acid sphingomyelinase"))."""
    parts = CONSTRUCT.split(ROUTE.sub("", label), 1)
    head, product = parts[0], parts[1] if len(parts) > 1 else ""

    def tidy(s):
        return " ".join(HEAD_FILLER.sub(" ", s).split()).strip(" ,-")
    return tidy(head), tidy(product)


def core_queries(gp: dict, causal: set[str]) -> list[dict]:
    """Up to two broader literature checks for a gap, as {names, also} (ANDed with the
    disease): the target with the modality ("activin A" + antibody), the modality alone
    for a gene-level modality on a causal gene, the construct's head and product."""
    label = gp["intervention_label"]
    mods = sorted(modalities(label))
    terms = [t for m in mods for t in MODALITY_TERMS[m]][:4]
    head, product = core_names(label)
    target = _strip(head)
    out = []
    if terms and len(target.replace(" ", "")) >= 4 and len(target.split()) <= 3:
        out.append({"names": [target], "also": terms})
    elif terms and gp["mechanism"] in causal and set(mods) & GENE_LEVEL:
        out.append({"names": terms, "also": []})
    if head and key(head) != key(label) and len(key(head)) >= 4:
        out.append({"names": [head], "also": [product] if len(key(product)) >= 4 else []})
    return out[:2]


def review(llm, profile_text: str, gaps: list[dict], max_review: int = 25) -> None:
    """Add plausibility / rationale / caveat / experiment to the open gaps (in place)."""
    todo = [x for x in gaps if x["status"] != "tested"][:max_review]
    if not todo or llm is None:
        return
    rows = []
    for i, x in enumerate(todo):
        rows.append(f"[{i}] intervention: {x['intervention_label']} ({x['kind']})\n"
                    f"  mechanism in input disease: {x['mechanism_label']} — "
                    + " | ".join(q[:300] for q in x["mechanism_evidence"]) +
                    f"\n  intervention evidence ({x['level']}): {x['intervention_edge']} — "
                    + " | ".join(q[:300] for q in x["intervention_evidence"]) +
                    f"\n  papers naming both: {x['literature']['count']}; trials: "
                    f"{len(x['trials'])}")
    try:
        out = llm.chat(REVIEW_SYSTEM, profile_text + "\n\nGAPS:\n" + "\n\n".join(rows),
                       max_tokens=16000)
    except Exception as e:
        print(f"  ! gap review: {type(e).__name__} {str(e)[:200]}")
        return
    for r in (out or {}).get("gaps") or []:
        try:
            x = todo[int(r.get("i"))]
            x["plausibility"] = max(0, min(3, int(r.get("plausibility"))))
        except (TypeError, ValueError, IndexError):
            continue
        for f in ("rationale", "caveat", "experiment"):
            x[f] = str(r.get(f) or "")[:400]


STATUS_ORDER = {"untested": 0, "barely_tested": 1, "unknown": 2, "tested": 3}


def rank(gaps: list[dict]) -> list[dict]:
    return sorted(gaps, key=lambda x: (STATUS_ORDER.get(x["status"], 9),
                                       -(x.get("plausibility") if x.get("plausibility")
                                         is not None else 1.5), -x["score"]))
