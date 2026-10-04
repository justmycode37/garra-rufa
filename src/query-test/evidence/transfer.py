"""Pass 3: an explicit transfer search outside the input disease.

The core papers come from the disease's own neighbourhood, so a solution from elsewhere
could only enter the graph as a tangent one of them mentions. This pass looks for them on
purpose, for any disease:

  1. mechanisms  the mechanism nodes the first graph establishes in the disease (genes,
                 pathways, processes, cell types; build.Graph.mechanisms: evidence edges
                 from the disease or its models, results obtained there, causal genes)
  2. plan        one LLM call picks the --transfer-nodes most promising of them and writes,
                 per node, searches for interventions acting on that mechanism in OTHER
                 diseases (each search = concept groups, AND of ORs, e.g. [ER stress |
                 unfolded protein response] AND [glycosylation disorder] AND [drug | rescue])
  3. search      Europe PMC title/abstract, the input disease's names excluded (NOT ...)
  4. screen      screen.py with the mechanism as extra context (papers need not mention the
                 input disease), best --transfer-papers per mechanism
  5. read        main.read_paper with the same context; the LLM names the mechanism as the
                 plan does, so the paper's "X involves M" edges meet the disease's own and
                 build.Graph.bridges() scores the solutions against that shared mechanism

Records carry origin "transfer" and the mechanism id; the plan, queries and hit counts go
to the output JSON ("transfer").

query_hygiene (../improvements.py): terms and excluded names are sanitised (context.
sanitize_terms: a synonym "Alstrom\\" broke every query), a search of three groups with
fewer than MIN_HITS hits is retried without its last (setting) group, and a mechanism that
ends without papers is reported.
"""
import sys

import improvements
from literature.europepmc import EuropePmcProvider, parse

from . import screen
from .context import key, sanitize_terms

MIN_HITS = 5

PLAN_SYSTEM = """You plan a literature search for a rare-disease research team. The team
has an evidence graph of the input disease; below are the mechanism nodes (genes,
pathways, processes, cell types) that the graph establishes in the disease, with their
support. Goal: find EXISTING interventions (drugs, therapies, chaperones, diets, gene or
RNA therapies, tool compounds) and models / assays that act on one of these mechanisms in
OTHER diseases or systems, so they could be transferred to the input disease.

Choose up to N mechanism nodes that are most promising for transfer: established in the
disease (not a passing mention), plausibly modifiable, and studied outside the disease
(other disorders of the same gene family / pathway / organelle / tissue, common diseases
with the same mechanism). Prefer specific mechanisms over generic ones ("inflammation",
"apoptosis") unless nothing better exists.

For each chosen node write 1-3 searches. A search is 2-3 concept groups that are ANDed;
each group is 1-6 synonyms (ORed) as they appear in titles/abstracts. One group names the
mechanism, one the intervention side (e.g. "treatment", "inhibitor", "rescue",
"chaperone", "drug repurposing"), optionally one the setting (related diseases sharing the
mechanism, e.g. other disorders of the same pathway). Never name the input disease itself
(it is excluded automatically). Keep terms short and literal; no boolean operators inside
terms.

Answer with one JSON object:
{"mechanisms": [{"i": <index>, "why": "one sentence",
  "searches": [{"description": "drugs that ... in ...",
                "groups": [["term", "synonym"], ["inhibitor", "treatment"]]}]}]}"""


def shortlist_text(mechs: list[dict]) -> str:
    lines = []
    for i, m in enumerate(mechs):
        ev = m["evidence"][0][:260] if m["evidence"] else ""
        lines.append(f"[{i}] {m['label']} ({m['kind']}; {len(m['papers'])} paper(s), "
                     f"confidence {m['confidence']}) {ev}")
    return "\n".join(lines)


def plan(llm, profile_text: str, mechs: list[dict], n: int) -> list[dict]:
    """[{id, label, kind, why, searches: [{description, groups}]}] for up to n mechanisms."""
    if not mechs:
        return []
    user = (f"{profile_text}\n\nMECHANISM NODES:\n{shortlist_text(mechs)}\n\n"
            f"Choose up to N = {n} nodes and write their searches.")
    out = llm.chat(PLAN_SYSTEM, user, max_tokens=16000)
    res = []
    for r in (out or {}).get("mechanisms") or []:
        try:
            m = mechs[int(r.get("i"))]
        except (TypeError, ValueError, IndexError):
            continue
        searches = []
        for s in r.get("searches") or []:
            groups = [[str(t).strip() for t in g if str(t).strip()][:6]
                      for g in (s or {}).get("groups") or [] if isinstance(g, list)]
            groups = [g for g in groups if g][:3]
            if len(groups) >= 2:
                searches.append({"description": str(s.get("description") or "")[:200],
                                 "groups": groups})
        if searches and all(x["id"] != m["id"] for x in res):
            res.append({"id": m["id"], "label": m["label"], "kind": m["kind"],
                        "why": str(r.get("why") or "")[:300], "searches": searches[:3]})
    return res[:n]


def _quote(t: str) -> str:
    return '"' + t.replace('"', "") + '"'


def epmc_query(groups: list[list[str]], exclude: list[str]) -> str:
    if improvements.on("query_hygiene"):
        groups = [g for g in (sanitize_terms(g, 2) for g in groups) if g]
        exclude = sanitize_terms(exclude)
    q = " AND ".join("(" + " OR ".join(f"TITLE_ABS:{_quote(t)}" for t in g) + ")"
                     for g in groups)
    if exclude:
        q += " NOT (" + " OR ".join(f"TITLE_ABS:{_quote(t)}" for t in exclude) + ")"
    return q


def exclusions(profile) -> list[str]:
    """Names of the input disease to exclude (long enough not to hit unrelated text)."""
    d = profile.disease
    names = d.get("names") or [d["label"]]
    if improvements.on("query_hygiene"):
        names = sanitize_terms(names)
    return [n for n in dict.fromkeys(names) if len(key(n)) > 4][:10]


def search(epmc: EuropePmcProvider, mech: dict, profile, per_query: int) -> tuple[list, list]:
    """(papers JSON dicts found for the mechanism's searches, query log). query_hygiene: a
    search of three groups with fewer than MIN_HITS hits is run again without its last
    (setting) group; a mechanism without any paper is reported on stderr."""
    papers, log, seen = [], [], set()
    hygiene = improvements.on("query_hygiene")
    for s in mech["searches"]:
        q = epmc_query(s["groups"], exclusions(profile))
        try:
            rows = epmc._search(q, per_query)
        except Exception as e:
            epmc.fail(f"transfer search {q[:80]}", e)
            rows = []
        log.append({"mechanism": mech["id"], "description": s["description"], "query": q,
                    "hits": len(rows)})
        if hygiene and len(rows) < MIN_HITS and len(s["groups"]) >= 3:
            q2 = epmc_query(s["groups"][:-1], exclusions(profile))
            try:
                more = epmc._search(q2, per_query)
            except Exception as e:
                epmc.fail(f"transfer search {q2[:80]}", e)
                more = []
            log.append({"mechanism": mech["id"], "description": s["description"]
                        + " (retry without the setting group)", "query": q2,
                        "hits": len(more), "retry": True})
            rows = rows + more
        for r in rows:
            p = parse(r).to_json()
            k = screen.paper_key(p)
            if k not in seen and (p.get("abstract") or p.get("pmcid")):
                seen.add(k)
                papers.append(p)
    if hygiene and not papers:
        print(f"  ! transfer: 0 papers for mechanism {mech['label']} ({mech['id']}) from "
              f"{len(log)} searches; check the query log (transfer.queries)",
              file=sys.stderr, flush=True)
    return papers, log


def context(profile_text: str, mech: dict, search_desc: str = "") -> str:
    """The profile plus the transfer task, as screen and extract see it."""
    return (f"{profile_text}\n\nTRANSFER SEARCH: these papers come from OUTSIDE the input "
            f"disease. Look for existing solutions acting on the mechanism "
            f"\"{mech['label']}\" ({mech['kind']}), which the evidence establishes in the "
            f"input disease ({mech['why']}). {search_desc}\n"
            "A paper is relevant (relevance 2-3) when it reports, with data, an intervention, "
            "model, assay or method that acts on or measures this mechanism in another "
            "disease or system; it does not need to mention the input disease. When "
            f"extracting, name this mechanism exactly \"{mech['label']}\" when the paper "
            "refers to it, and include the edges that link the paper's disease or model to "
            "the mechanism (involves) and the intervention to it (rescues / targets).")
