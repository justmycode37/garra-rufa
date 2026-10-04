#!/usr/bin/env python3
"""Collect research papers for a disease graph (second pipeline).

Input is the graph JSON written by ../main.py (-o <file>.json): its nodes carry
descriptions, synonyms, MeSH headings and cited papers (Node.info), its edges the papers
backing them (Edge.evidence). This script

  1. harvests the papers the graph sources already cite (harvest.py)
  2. plans literature queries for the focus disease(s) and the genes, drugs, phenotypes,
     variants and subtypes the graph links to them (plan.py)
  3. runs each query against the providers that understand it:
       pubmed     NCBI E-utilities, MeSH + title/abstract queries
       europepmc  Europe PMC REST: title/abstract, most cited, preprints
       pubtator   PubTator 3 concept search (@DISEASE_x AND @GENE_y)
       litvar     LitVar2: papers mentioning a variant
     until --max-papers new papers are collected
  4. fills in metadata for every paper: citation counts / open access (Europe PMC),
     abstract, MeSH, publication types (PubMed efetch), entity annotations and relations
     (PubTator export)

Papers are deduplicated by PMID / DOI / PMCID; each keeps every hit (provider, query,
graph entity, relation, rank) that found it, for linking and sorting in a later step.
Raw API responses are cached in data/literature-cache/ (--refresh ignores the cache).

Usage:
  python src/query-test/main.py "Marfan syndrome" -o runs/marfan.json
  python src/query-test/literature/main.py runs/marfan.json -o runs/marfan.papers.json
  python src/query-test/literature/main.py runs/marfan.json --providers pubmed litvar harvest
  python src/query-test/evidence/main.py runs/marfan.papers.json   # -> evidence graph
"""
import argparse
import json
import sys
import time
from collections import Counter
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/query-test

try:  # use the OS trust store; the certifi bundle fails on this machine
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from report import Stats  # noqa: E402

from literature import _MODULES, load_providers  # noqa: E402
from literature.base import Cache, Collection  # noqa: E402
from literature.harvest import harvest  # noqa: E402
from literature.plan import MeshLookup, plan  # noqa: E402


def collect(graph: dict, providers, cache: Cache, max_papers: int, use_harvest: bool,
            annotations: bool, stats: Stats) -> tuple[Collection, list[dict], list]:
    coll = Collection()
    if use_harvest:
        for p in harvest(graph):
            coll.add(p)
        print(f"harvest: {len(coll)} papers cited by the graph sources")
    base = len(coll)

    mesh = MeshLookup(cache)
    queries = plan(graph, mesh)
    print(f"plan: {len(queries)} queries")
    log: list[dict] = []
    for i, q in enumerate(queries):
        if len(coll) - base >= max_papers:
            print(f"--max-papers {max_papers} reached; {len(queries) - i} queries not run")
            log += [{"id": x.id, "relation": x.relation, "entity": x.entity, "skipped": True}
                    for x in queries[i:]]
            break
        for prov in providers:
            if not prov.handles(q):
                continue
            rec, t0 = stats.src[prov.name], time.time()
            text, papers = prov.search(q)
            if not text:
                continue
            new = sum(coll.add(p) for p in papers)
            rec["calls"] += 1
            rec["edges"] += len(papers)  # (report.Stats naming: results)
            rec["empty"] += not papers
            rec["seconds"] += time.time() - t0
            rec["new"] = rec.get("new", 0) + new
            log.append({"id": q.id, "relation": q.relation, "entity": q.entity,
                        "provider": prov.name, "query": text, "results": len(papers),
                        "new": new})
            print(f"  [{len(coll):5d}] {prov.name:9s} {q.relation:18s} "
                  f"{(q.other or q.disease).label[:40]:40s} {len(papers):4d} hits, {new:4d} new")

    by_name = {p.name: p for p in providers}
    for name in ("europepmc", "pubmed", *(["pubtator"] if annotations else [])):
        if name in by_name:
            print(f"enrich: {name}")
            t0 = time.time()
            by_name[name].enrich(coll)
            stats.src[name]["seconds"] += time.time() - t0
    return coll, log, queries


def _score(p) -> tuple:
    """Output order only (sorting proper is a later step): found for more kinds of
    reasons (relations) and by more providers first, then by citations. Not by entity
    count: ClinVar cites guideline papers for dozens of variants."""
    return (-len({h.relation for h in p.hits}), -len({h.provider for h in p.hits}),
            -(p.cited_by or 0), p.pmid or "")


def summary(coll: Collection, providers: list[str]):
    found = {name: {id(p) for p in coll.papers if any(h.provider == name for h in p.hits)}
             for name in providers}
    found["graph"] = {id(p) for p in coll.papers if any(h.provider.startswith("graph:")
                                                        for h in p.hits)}
    print(f"\n=== {len(coll)} papers ===")
    for name, ids in found.items():
        print(f"  {name:10s} {len(ids):5d}  only here: "
              f"{len(ids - set().union(*(v for k, v in found.items() if k != name)))}")
    print("  overlap: " + ", ".join(f"{a}&{b} {len(found[a] & found[b])}"
                                    for a, b in combinations(found, 2)))
    with_abs = sum(bool(p.abstract) for p in coll.papers)
    print(f"  with abstract {with_abs}, with annotations "
          f"{sum(bool(p.annotations) for p in coll.papers)}, open access "
          f"{sum(bool(p.open_access) for p in coll.papers)}")
    rels = Counter(h.relation for p in coll.papers for h in p.hits)
    print("  hits by relation: " + ", ".join(f"{r} {n}" for r, n in rels.most_common()))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("graph", type=Path, help="graph JSON from ../main.py -o <file>.json")
    ap.add_argument("-o", "--out", type=Path, help="papers JSON (default <graph>.papers.json)")
    ap.add_argument("--providers", nargs="*", choices=[*_MODULES, "harvest"],
                    help="only these providers (default: all, plus harvest)")
    ap.add_argument("--max-papers", type=int, default=3000,
                    help="stop running queries once this many new papers are collected "
                         "(papers cited by the graph do not count)")
    ap.add_argument("--no-annotations", action="store_true",
                    help="skip the PubTator annotation export")
    ap.add_argument("--refresh", action="store_true", help="ignore cached API responses")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    out = args.out or args.graph.with_suffix(".papers.json")
    cache = Cache(refresh=args.refresh)
    only = [p for p in args.providers or [] if p != "harvest"] or None
    if args.providers is not None and not only:
        only = ["none"]
    providers = load_providers(cache, only)
    use_harvest = args.providers is None or "harvest" in args.providers
    print("providers:", ", ".join(p.name for p in providers) or "(none)",
          "+ harvest" if use_harvest else "")
    stats = Stats()
    for p in providers:
        stats.instrument(p)

    t0 = time.time()
    coll, log, queries = collect(graph, providers, cache, args.max_papers, use_harvest,
                                 not args.no_annotations, stats)
    papers = sorted(coll.papers, key=_score)
    nodes = {n["id"]: n for n in graph["nodes"]}
    pubtator = next((p for p in providers if p.name == "pubtator"), None)
    data = {
        "graph": str(args.graph),
        "start": graph["start"],
        "focus": [{"id": f, "label": nodes[f]["label"]} for f in graph.get("focus") or []
                  if f in nodes],
        "settings": {"max_papers": args.max_papers, "providers": [p.name for p in providers],
                     "harvest": use_harvest},
        "queries": log,
        "concepts": {c.id: {k: v for k, v in vars(c).items() if v not in (None, [], 0, False)}
                     for q in queries for c in (q.disease, q.other) if c},
        "related_concepts": pubtator.related if pubtator else {},
        "stats": {"seconds": round(time.time() - t0), "papers": len(papers),
                  "cache_hits": cache.hits, "requests": cache.misses,
                  "providers": {k: {**{x: y for x, y in v.items()
                                       if x not in ("http", "http_errors", "raised")},
                                    "http": dict(v["http"]),
                                    "errors": next((p.errors for p in providers
                                                    if p.name == k), [])[:20]}
                                for k, v in stats.src.items()}},
        "papers": [p.to_json() for p in papers],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")
    summary(coll, [p.name for p in providers])
    print(f"\n{len(papers)} papers -> {out.resolve()}  ({time.time() - t0:.0f}s, "
          f"{cache.misses} requests, {cache.hits} from cache)")


if __name__ == "__main__":
    main()
