#!/usr/bin/env python3
"""Evidence-first knowledge graph of existing solutions for a rare disease (third pipeline).

Input is the papers JSON of ../literature/main.py (and, through its "graph" field, the
graph JSON of ../main.py). Two LLM passes (OpenRouter, llm.py; OPENROUTER_API_KEY):

  1. screen   titles + abstracts in batches: which papers connect an existing solution
              (drug, therapy, disease model, biomarker, assay, diagnostic, outcome
              measure, resource, method) to the input disease or to a disease sharing
              its mechanism (screen.py) -> the --max-fulltext most relevant
  2. extract  each selected paper's full text (Europe PMC / PMC BioC, else abstract;
              fulltext.py) -> entities and edges, each with verbatim quotes (extract.py)

Quotes are checked against the text (verify.py; edges without a verified quote are
dropped), entities mapped to graph / MONDO / Orphanet / HP / UBERON / CL / CHEBI / GO /
HGNC ids (normalize.py), and the edges merged, scored and turned into ranked candidate
solutions for the input disease with the path that justifies each (build.py).

Outputs: <out>.json (everything, incl. all screening decisions), <out>.md (ranked
candidates with quotes), <out>.html (graph viewer). LLM and HTTP responses are cached in
data/literature-cache/, so a rerun costs no calls (--refresh ignores the cache).

Usage:
  python src/query-test/literature/main.py runs/pmm2.json -o runs/pmm2.papers.json
  python src/query-test/evidence/main.py runs/pmm2.papers.json -o runs/pmm2.kg.json
  python src/query-test/evidence/main.py runs/pmm2.papers.json --max-screen 60 --max-fulltext 5
  python src/query-test/evidence/main.py runs/pmm2.papers.json --dry-run
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/query-test

try:  # use the OS trust store; the certifi bundle fails on this machine
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from literature.base import Cache  # noqa: E402

from evidence import extract, screen  # noqa: E402
from evidence.build import Graph  # noqa: E402
from evidence.context import build_profile  # noqa: E402
from evidence.fulltext import FullTextProvider  # noqa: E402
from evidence.llm import Llm  # noqa: E402
from evidence.normalize import Normalizer  # noqa: E402
from evidence.verify import Verifier  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def find_graph(papers_path: Path, papers: dict, override: Path | None) -> Path:
    if override:
        return override
    g = Path(papers.get("graph") or "")
    for cand in (g, ROOT / g, papers_path.parent / g.name):
        if str(g) and cand.exists():
            return cand
    raise SystemExit(f"graph JSON {g} not found; pass --graph")


def run_pool(fn, items, workers: int, label: str):
    """[(item, result | Exception)] in input order, with a progress line per item."""
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(fn, it): i for i, it in enumerate(items)}
        for done, f in enumerate(as_completed(futs), 1):
            i = futs[f]
            try:
                results[i] = f.result()
            except Exception as e:  # one failed call must not kill the run
                results[i] = e
                print(f"  ! {label} {i}: {type(e).__name__} {str(e)[:200]}", file=sys.stderr)
            print(f"  {label} {done}/{len(items)}", flush=True)
    return results


# -- pass 1 ------------------------------------------------------------------------
def pass_screen(llm, profile_text, papers, batch_size, workers) -> dict[str, dict]:
    batches = [papers[i:i + batch_size] for i in range(0, len(papers), batch_size)]
    decisions: dict[str, dict] = {}
    res = run_pool(lambda b: screen.screen_batch(llm, profile_text, b), batches, workers,
                   "screen batch")
    retry = []
    for b, r in zip(batches, res):
        if isinstance(r, Exception):
            continue
        decisions.update(r)
        retry += [p for p in b if screen.paper_key(p) not in r]
    if retry:  # papers the model skipped in a batch: once more, in smaller batches
        print(f"screen: {len(retry)} papers missing from replies, retrying")
        small = [retry[i:i + 5] for i in range(0, len(retry), 5)]
        for r in run_pool(lambda b: screen.screen_batch(llm, profile_text, b), small, workers,
                          "screen retry"):
            if not isinstance(r, Exception):
                decisions.update(r)
    return decisions


# -- pass 2 ------------------------------------------------------------------------
def read_paper(paper, *, llm, profile_text, fulltext, normalizer, max_chars) -> dict:
    key = screen.paper_key(paper)
    doc = fulltext.get(paper, max_chars)
    out = llm.chat(extract.SYSTEM, extract.prompt(profile_text, paper, doc), max_tokens=24000)
    ents, edges, st = extract.clean_result(out)
    ver = Verifier(doc)
    passages = doc.by_id()
    kept, quotes, bad_quotes = [], 0, 0
    for e in edges:
        ev = []
        for q in e["evidence"]:
            quotes += 1
            pid = ver.check(q["quote"], q["passage"])
            if pid:
                ev.append({"passage": pid, "section": passages[pid].section, "quote": q["quote"]})
            else:
                bad_quotes += 1
        if ev:
            kept.append({**e, "evidence": ev})
    used = {k for e in kept for k in (e["subject"], e["object"])}
    ann = paper.get("annotations") or []
    for k in used:
        ents[k]["norm"] = normalizer.resolve(ents[k]["name"], ents[k]["type"],
                                             ents[k]["synonyms"], ann)
    return {"key": key, "text_source": doc.source, "truncated": doc.truncated,
            "chars": doc.chars(), "summary": str((out or {}).get("summary") or "")[:800],
            "meta": {k: paper.get(k) for k in ("pmid", "pmcid", "doi", "title", "year",
                                               "journal", "cited_by")},
            "entities": {k: v for k, v in ents.items() if k in used}, "edges": kept,
            "stats": {**st, "quotes": quotes, "unverified_quotes": bad_quotes,
                      "edges_kept": len(kept), "edges_dropped_unverified": len(edges) - len(kept)}}


# -- outputs -----------------------------------------------------------------------
def write_html(g: Graph, candidates: list[dict], path: Path):
    """Embed the evidence graph into ../viewer.html (edge source = evidence level)."""
    top = {c["id"] for c in candidates[:40]}
    data = {"start": g.profile.id,
            "nodes": [{"id": n["id"], "label": n["label"], "kind": n["kind"],
                       "sources": ["papers"] if n["papers"] else ["graph"]}
                      for n in g.nodes.values()],
            "edges": [{"from": e["from"], "to": e["to"], "relation": e["relation"],
                       "source": e["level"]} for e in g.edges.values()]
            + [{"from": c["id"], "to": g.profile.id, "relation": "may_accelerate",
                "source": "inferred"} for c in candidates if c["id"] in top]}
    template = (Path(__file__).resolve().parents[1] / "viewer.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = re.sub(r"/\*__DATA__\*/.*?/\*__END__\*/", lambda _: payload, template, flags=re.S)
    path.write_text(html.replace("<title>Query Graph</title>", "<title>Evidence Graph</title>"),
                    encoding="utf-8")


def write_md(data: dict, path: Path):
    p = data["profile"]["disease"]
    L = [f"# Existing solutions for {p['label']} ({p['id']})", "",
         f"Model `{data['settings']['model']}` · screened {data['stats']['screened']} papers "
         f"· {data['stats']['selected']} read · {len(data['nodes'])} nodes · "
         f"{len(data['edges'])} evidence edges · {len(data['candidates'])} candidates", "",
         "Scores combine edge evidence (level, number of papers) with how the solution's "
         "target connects to the input disease. *direct*: already applied to the input "
         "disease; *transfer*: from a related disease / shared mechanism.", ""]
    for cat, title in (("direct", "Already applied to the input disease"),
                       ("transfer", "Transfer candidates")):
        cs = [c for c in data["candidates"] if c["category"] == cat]
        L += [f"## {title} ({len(cs)})", ""]
        for i, c in enumerate(cs[:40], 1):
            flag = " — **tested without benefit in the input disease**" \
                if c["tested_without_benefit_in_input"] else ""
            L.append(f"### {i}. {c['label']} ({c['kind']}, score {c['score']}){flag}")
            L.append(f"`{c['id']}` · papers: {', '.join(c['papers'][:8])}")
            L.append("")
            for x in c["paths"][:4]:
                neg = f", {x['negative_papers']} negative/null" if x["negative_papers"] else ""
                L.append(f"- **{x['edge']}** — {x['level']}, {x['papers']} paper(s){neg}, "
                         f"confidence {x['confidence']}; bridge: {x['bridge']} "
                         f"(×{x['bridge_weight']})")
                for q in x["evidence"][:2]:
                    L.append(f"  - {q}")
                for q in x["bridge_evidence"][:1]:
                    L.append(f"  - bridge: {q}")
            L.append("")
    L += ["## Evidence edges by predicate", ""]
    by = Counter(e["relation"] for e in data["edges"])
    labels = {n["id"]: n["label"] for n in data["nodes"]}
    for rel, n in by.most_common():
        L += [f"### {rel} ({n})", ""]
        es = sorted((e for e in data["edges"] if e["relation"] == rel),
                    key=lambda e: -e["confidence"])
        for e in es[:30]:
            ev = e["evidence"][0]
            L.append(f"- {labels.get(e['from'], e['from'])} → {labels.get(e['to'], e['to'])} "
                     f"({e['level']}, {e['papers']} paper(s), conf {e['confidence']}): "
                     f"\"{ev['quote'][:240]}\" ({ev['paper']})")
        L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("papers", type=Path, help="papers JSON from ../literature/main.py")
    ap.add_argument("-o", "--out", type=Path, help="output JSON (default <papers>.kg.json "
                                                     "next to it; .md and .html alongside)")
    ap.add_argument("--graph", type=Path, help="graph JSON (default: the papers' 'graph')")
    ap.add_argument("--model", help="OpenRouter model (default $OPENROUTER_MODEL or "
                                    "stealth/space-bunny-alpha)")
    ap.add_argument("--pass", dest="passes", choices=["screen", "all"], default="all",
                    help="screen only, or screen + extract (default)")
    ap.add_argument("--max-screen", type=int, help="screen only the first N papers")
    ap.add_argument("--batch", type=int, default=screen.BATCH, help="papers per screening call")
    ap.add_argument("--min-relevance", type=int, default=2)
    ap.add_argument("--max-fulltext", type=int, default=50, help="papers read in pass 2")
    ap.add_argument("--max-chars", type=int, default=150_000, help="text per paper in pass 2")
    ap.add_argument("--workers", type=int, default=4, help="parallel LLM calls")
    ap.add_argument("--refresh", action="store_true", help="ignore cached API/LLM responses")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the profile and first prompt of each pass; no LLM calls")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    papers_doc = json.loads(args.papers.read_text(encoding="utf-8"))
    graph_path = find_graph(args.papers, papers_doc, args.graph)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    name = args.papers.name.removesuffix(".json").removesuffix(".papers")
    out = args.out or args.papers.with_name(name + ".kg.json")
    t0 = time.time()

    profile = build_profile(graph, papers_doc)
    ptext = profile.text()
    cache = Cache(refresh=args.refresh)
    llm = Llm(cache, args.model)
    fulltext = FullTextProvider(cache)
    normalizer = Normalizer(cache, profile)
    papers = papers_doc["papers"][:args.max_screen] if args.max_screen else papers_doc["papers"]
    print(f"input disease: {profile.disease['label']} ({profile.id}); graph {graph_path}")
    print(f"model: {llm.model}; {len(papers)} papers to screen")

    if args.dry_run:
        print("\n=== profile ===\n" + ptext)
        print("\n=== pass 1 system ===\n" + screen.SYSTEM)
        print("\n=== pass 1 prompt (first batch) ===\n"
              + screen.prompt(ptext, papers[:args.batch])[:6000])
        p = next((x for x in papers if x.get("pmcid") and x.get("open_access")), papers[0])
        doc = fulltext.get(p, args.max_chars)
        print(f"\n=== pass 2 system ===\n{extract.SYSTEM}")
        print(f"\n=== pass 2 prompt ({screen.paper_key(p)}, {doc.source}, "
              f"{len(doc.passages)} passages, {doc.chars()} chars) ===\n"
              + extract.prompt(ptext, p, doc)[:6000] + "\n...")
        return
    if not llm.key:
        raise SystemExit("OPENROUTER_API_KEY is not set")

    # pass 1
    print(f"\n== pass 1: screening {len(papers)} abstracts ==")
    decisions = pass_screen(llm, ptext, papers, args.batch, args.workers)
    selected = screen.select(papers, decisions, args.min_relevance, args.max_fulltext)
    inc = sum(d["include"] for d in decisions.values())
    print(f"screened {len(decisions)}/{len(papers)}: {inc} included, "
          f"{len(selected)} selected for full text")

    records = []
    if args.passes == "all" and selected:
        print(f"\n== pass 2: reading {len(selected)} papers ==")
        res = run_pool(lambda p: read_paper(p, llm=llm, profile_text=ptext, fulltext=fulltext,
                                            normalizer=normalizer, max_chars=args.max_chars),
                       selected, args.workers, "paper")
        records = [r for r in res if isinstance(r, dict)]
        failed = [screen.paper_key(p) for p, r in zip(selected, res) if not isinstance(r, dict)]
    else:
        failed = []

    g = Graph(profile)
    for r in records:
        g.add_paper(r)
    g.score_edges()
    candidates = g.candidates()

    rstats = Counter()
    for r in records:
        rstats.update(r["stats"])
    data = {
        "papers_file": str(args.papers), "graph": str(graph_path),
        "settings": {"model": llm.model, "max_screen": args.max_screen,
                     "min_relevance": args.min_relevance, "max_fulltext": args.max_fulltext,
                     "max_chars": args.max_chars},
        "profile": {k: getattr(profile, k) for k in ("disease", "genes", "phenotypes",
                                                       "pathways", "drugs", "related")},
        "stats": {"seconds": round(time.time() - t0), "screened": len(decisions),
                  "included": inc, "selected": len(selected), "read": len(records),
                  "failed": failed, "llm": llm.stats,
                  "text_sources": dict(Counter(r["text_source"] for r in records)),
                  "extraction": dict(rstats), "normalization": normalizer.counts,
                  "http": {"requests": cache.misses, "cache_hits": cache.hits},
                  "errors": (fulltext.errors + normalizer.errors)[:30]},
        "candidates": candidates,
        "nodes": sorted(g.nodes.values(), key=lambda n: (n["kind"], n["label"])),
        "edges": sorted(g.edges.values(), key=lambda e: -e["confidence"]),
        "papers": [{k: v for k, v in r.items() if k not in ("entities", "edges")}
                   for r in records],
        "screening": [{"key": screen.paper_key(p), "title": p.get("title"),
                       **decisions.get(screen.paper_key(p), {"error": "not screened"})}
                      for p in papers],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    write_md(data, out.with_suffix(".md"))
    write_html(g, candidates, out.with_suffix(".html"))

    print(f"\n=== {len(g.nodes)} nodes, {len(g.edges)} evidence edges, "
          f"{len(candidates)} candidate solutions ===")
    print(f"  text: {data['stats']['text_sources']}; quotes {rstats['quotes']}, unverified "
          f"{rstats['unverified_quotes']}; edges dropped unverified "
          f"{rstats['edges_dropped_unverified']}")
    print(f"  normalization: {normalizer.counts}")
    print(f"  llm: {llm.stats['calls']} calls, {llm.stats['cached']} cached, "
          f"{llm.stats['prompt_tokens']} + {llm.stats['completion_tokens']} tokens")
    for c in candidates[:15]:
        print(f"  {c['score']:.3f} {c['category']:8s} {c['kind']:15s} {c['label'][:40]:40s} "
              f"{c['paths'][0]['edge'][:60]}")
    print(f"\n-> {out.resolve()} (+ .md, .html)  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
