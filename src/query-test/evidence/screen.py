"""Pass 1: screen titles + abstracts for papers that connect an existing solution to the
input disease.

Papers go to the LLM in batches (BATCH) with the disease profile. Per paper it returns

  include        bool
  relevance      0 unrelated / background only ... 3 reports a solution with data in the
                 input disease or a disease sharing its mechanism
  connection     same_disease | related_disease | shared_mechanism | shared_phenotype |
                 method_transfer | none
  solution_types drug, therapy, model_system, biomarker, assay, diagnostic,
                 outcome_measure, resource, method
  solutions      names of the solutions the abstract mentions
  why            one sentence

select() keeps include && relevance >= min_relevance, best first (relevance, full text
available, citations), up to max_fulltext papers for pass 2. diverse_selection
(../improvements.py): within each relevance level a paper naming a solution not covered
yet goes first (diverse()), so the read set is not 30 papers on one drug.
"""
import re

import improvements

from .fulltext import clean

BATCH = 15
ABSTRACT_CHARS = 2500
CONNECTIONS = ("same_disease", "related_disease", "shared_mechanism", "shared_phenotype",
               "method_transfer", "none")
SOLUTION_TYPES = ("drug", "therapy", "model_system", "biomarker", "assay", "diagnostic",
                  "outcome_measure", "resource", "method")

SYSTEM = f"""You screen biomedical literature for a rare-disease research team.

Goal: find papers that report EXISTING SOLUTIONS that could accelerate research on the input
disease. A solution is something already developed or tested that the team could reuse:
- drug: small molecules, repurposed drugs, supplements (e.g. sugars, cofactors)
- therapy: gene therapy, ASO, enzyme replacement, transplant, diet, chaperones, other non-drug treatment
- model_system: animal models, patient cell lines, iPSC, organoids, yeast/worm/fly models
- biomarker / assay / diagnostic: measurable markers, screening assays, diagnostic tests
- outcome_measure: clinical rating scales, natural-history endpoints, trial endpoints
- resource: registries, biobanks, datasets, consortia
- method: experimental or computational techniques (drug screens, structural modelling, ...)

A paper is relevant when it reports such a solution, with data, for the input disease OR for
another disease that shares its gene, pathway, biological process, cell type, tissue or key
phenotypes (then the solution may transfer). Mechanistic papers that establish such a shared
mechanism are relevant too.

Relevance:
3 = reports a solution with own data in the input disease or a mechanistically close disease
2 = reports a solution in a related disease/mechanism that plausibly transfers, or provides
    substantive evidence on the shared mechanism
1 = background only (epidemiology, plain case/genotype description without intervention,
    generic overview)
0 = unrelated

Judge only from the given title/abstract; do not use outside knowledge to invent results.
Answer with one JSON object:
{{"papers": [{{"key": "<the key given>", "include": true, "relevance": 0-3,
  "connection": one of {list(CONNECTIONS)},
  "solution_types": subset of {list(SOLUTION_TYPES)},
  "solutions": ["short names of the concrete solutions"],
  "why": "one sentence"}}]}}
Return exactly one entry per paper, in the given order."""


def paper_key(p: dict) -> str:
    if p.get("pmid"):
        return f"PMID:{p['pmid']}"
    if p.get("pmcid"):
        return f"PMC:{p['pmcid']}"
    return f"DOI:{p.get('doi')}"


def render(p: dict) -> str:
    mesh = [m.lstrip("*").split("/")[0] for m in p.get("mesh") or [] if m.startswith("*")]
    head = [f"key: {paper_key(p)}", f"title: {clean(p.get('title')) or '(none)'}"]
    meta = ", ".join(str(x) for x in [p.get("year"), p.get("journal"),
                                      "/".join(str(t) for t in (p.get("pub_types") or [])[:3]
                                               if t)] if x)
    if meta:
        head.append(f"meta: {meta}")
    if mesh:
        head.append("mesh: " + "; ".join(mesh[:10]))
    if p.get("keywords"):
        head.append("keywords: " + "; ".join(str(k) for k in p["keywords"][:10] if k))
    ab = clean(p.get("abstract"))
    head.append("abstract: " + (ab[:ABSTRACT_CHARS] + (" ..." if len(ab) > ABSTRACT_CHARS
                                                        else "") if ab else "(no abstract)"))
    return "\n".join(head)


def prompt(profile_text: str, batch: list[dict]) -> str:
    return (profile_text + "\n\nPAPERS:\n\n" + "\n\n---\n\n".join(render(p) for p in batch)
            + f"\n\nScreen all {len(batch)} papers.")


def _norm(r: dict) -> dict:
    try:
        rel = int(r.get("relevance") or 0)
    except (TypeError, ValueError):
        rel = 0
    conn = r.get("connection") if r.get("connection") in CONNECTIONS else "none"
    return {"include": bool(r.get("include")) and rel > 0, "relevance": max(0, min(3, rel)),
            "connection": conn,
            "solution_types": [t for t in r.get("solution_types") or [] if t in SOLUTION_TYPES],
            "solutions": [str(s) for s in r.get("solutions") or []][:12],
            "why": str(r.get("why") or "")[:400]}


def _keyish(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).upper()


def screen_batch(llm, profile_text: str, batch: list[dict]) -> dict[str, dict]:
    """key -> decision for one batch (papers the model skipped are missing)."""
    out = llm.chat(SYSTEM, prompt(profile_text, batch), max_tokens=32000)
    rows = out.get("papers") if isinstance(out, dict) else out
    keys = {_keyish(paper_key(p)): paper_key(p) for p in batch}
    res = {}
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict):
            continue
        k = keys.get(_keyish(r.get("key")))
        if k is None and len(rows) == len(batch):  # key garbled: fall back to position
            k = paper_key(batch[i])
        if k:
            res[k] = _norm(r)
    return res


def select(papers: list[dict], decisions: dict[str, dict], min_relevance: int,
           max_fulltext: int, known: list[str] = ()) -> list[dict]:
    """known: the profile's known drugs / trial drugs (diverse_selection reserves a paper
    for each first)."""
    keep = [p for p in papers if (d := decisions.get(paper_key(p))) and d["include"]
            and d["relevance"] >= min_relevance]
    keep.sort(key=lambda p: (-decisions[paper_key(p)]["relevance"],
                             not (p.get("pmcid") and p.get("open_access")),
                             not p.get("abstract"), -(p.get("cited_by") or 0)))
    if improvements.on("diverse_selection"):
        return diverse(keep, decisions, max_fulltext, known)
    return keep[:max_fulltext]


def solution_keys(d: dict) -> set[str]:
    """What a screened paper is about: its solutions' names (canonical keys), else its
    solution types ("type:drug")."""
    from .build import canon_key
    ks = {canon_key(s, "drug") for s in d.get("solutions") or []} - {""}
    return ks or {f"type:{t}" for t in d.get("solution_types") or []} or {"none"}


def reserved(ranked: list[dict], decisions: dict[str, dict], n: int,
             known: list[str] = ()) -> list[dict]:
    """Papers diverse() picks first (at most 2/3 of n): the best paper naming each known
    drug / trial drug of the profile, then the best paper per solution of the papers
    screened as solutions tested in the input itself (same_disease, relevance 3)."""
    from .build import canon_key
    keys = {paper_key(p): solution_keys(decisions[paper_key(p)]) for p in ranked}
    out: list[dict] = []
    cap = max(1, n * 2 // 3)

    def take(want: str) -> None:
        for p in ranked:
            if len(out) >= cap or p in out:
                continue
            ks = keys[paper_key(p)]
            if want in ks or any(f" {want} " in f" {k} " for k in ks if not k.startswith(
                    "type:")):
                out.append(p)
                return
    for k in dict.fromkeys(canon_key(x, "drug") for x in known):
        if k and len(k) >= 4:
            take(k)
    tested = [k for p in ranked for k in sorted(keys[paper_key(p)])
              if decisions[paper_key(p)]["relevance"] == 3 and not k.startswith("type:")
              and decisions[paper_key(p)].get("connection") == "same_disease"]
    covered = {k for p in out for k in keys[paper_key(p)]}
    for k in dict.fromkeys(tested):
        if k not in covered:
            take(k)
            covered |= {x for p in out for x in keys[paper_key(p)]}
    return out


def diverse(ranked: list[dict], decisions: dict[str, dict], n: int,
            known: list[str] = ()) -> list[dict]:
    """diverse_selection: up to n papers of `ranked` (best first): the reserved() ones,
    then level by level of relevance the best paper that names a solution not covered
    yet, else the best remaining one, so the read set covers more distinct solutions."""
    out = reserved(ranked, decisions, n, known)[:n]
    covered = {k for p in out for k in solution_keys(decisions[paper_key(p)])}
    for rel in sorted({decisions[paper_key(p)]["relevance"] for p in ranked}, reverse=True):
        pool = [p for p in ranked if decisions[paper_key(p)]["relevance"] == rel
                and p not in out]
        keys = {paper_key(p): solution_keys(decisions[paper_key(p)]) for p in pool}
        while pool and len(out) < n:
            p = next((x for x in pool if keys[paper_key(x)] - covered), pool[0])
            pool.remove(p)
            covered |= keys[paper_key(p)]
            out.append(p)
    return out
