#!/usr/bin/env python3
"""Expand one medical input term into a graph of related nodes across datasets.

Each iteration runs every node from the previous iteration (the frontier) through
every source in sources/, collecting the connected nodes as the next frontier.
No deduping yet: nodes may appear multiple times.

Usage:
  python src/query-test/main.py "Marfan syndrome"
  python src/query-test/main.py "Marfan syndrome" -n 3 --limit 5 --sources mondo hpo
  python src/query-test/main.py MONDO:0007947 --label "Marfan syndrome" -o graph.png
"""
import argparse
import re
import sys
from pathlib import Path

import networkx as nx

try:  # use the OS trust store; the certifi bundle fails on this machine
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from sources import Edge, Node, load_sources

CURIE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*:\S+$")


def parse_input(text: str, label: str | None) -> Node:
    if CURIE.match(text):
        return Node(label=label or text, id=text, kind="unknown")
    return Node(label=text, kind="term")


def run(start: Node, sources, iterations: int, limit: int, max_frontier: int) -> list[Edge]:
    edges: list[Edge] = []
    frontier = [start]
    for i in range(1, iterations + 1):
        if len(frontier) > max_frontier:
            print(f"(frontier of {len(frontier)} capped to {max_frontier})")
            frontier = frontier[:max_frontier]
        found: list[Edge] = []
        for node in frontier:
            for src in sources:
                try:
                    found += src.query(node, limit=limit)
                except Exception as e:  # sources shouldn't raise, but don't let one kill the run
                    print(f"  ! {src.name} failed on {node.label}: {e}", file=sys.stderr)
        print(f"\n=== Iteration {i}: {len(frontier)} inputs -> {len(found)} nodes ===")
        for e in found:
            print(f"  {e.src.label}  --{e.relation}-->  {e.dst}")
        edges += found
        frontier = [e.dst for e in found]
        if not frontier:
            break
    return edges


def build_graph(edges: list[Edge]) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for e in edges:
        for n in (e.src, e.dst):
            if n.key() in g:  # same node reported by several sources: keep all of them
                d = g.nodes[n.key()]
                d["sources"] |= {n.source}
                d["xrefs"] |= set(n.xrefs)
                if d["kind"] in ("unknown", "term"):
                    d["kind"] = n.kind
            else:
                g.add_node(n.key(), label=n.label, kind=n.kind, source=n.source,
                           sources={n.source}, xrefs=set(n.xrefs))
        g.add_edge(e.src.key(), e.dst.key(), relation=e.relation, source=e.source)
    return g


def write_html(g: nx.MultiDiGraph, start: Node, path: Path):
    """Embed the graph as JSON into viewer.html (interactive vis-network page)."""
    import json
    data = {
        "start": start.key(),
        "nodes": [{"id": k, "label": d["label"], "kind": d["kind"],
                   "sources": sorted(d["sources"]), "xrefs": sorted(d["xrefs"])}
                  for k, d in g.nodes(data=True)],
        "edges": [{"from": u, "to": v, "relation": d["relation"], "source": d["source"]}
                  for u, v, d in g.edges(data=True)],
    }
    template = (Path(__file__).parent / "viewer.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = re.sub(r"/\*__DATA__\*/.*?/\*__END__\*/", lambda _: payload, template, flags=re.S)
    path.write_text(html, encoding="utf-8")
    print(f"graph viewer -> {path.resolve()}")


def draw(g: nx.MultiDiGraph, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    kinds = sorted({d["kind"] for _, d in g.nodes(data=True)})
    cmap = plt.get_cmap("tab10")
    colors = [cmap(kinds.index(d["kind"]) % 10) for _, d in g.nodes(data=True)]
    size = max(10, min(40, len(g) ** 0.5 * 3))
    fig, ax = plt.subplots(figsize=(size, size))
    pos = nx.spring_layout(g, seed=1, k=1.5 / max(len(g), 1) ** 0.5)
    nx.draw_networkx_edges(g, pos, ax=ax, alpha=0.3, arrows=False)
    nx.draw_networkx_nodes(g, pos, ax=ax, node_color=colors, node_size=60)
    nx.draw_networkx_labels(g, pos, {n: d["label"][:30] for n, d in g.nodes(data=True)}, ax=ax, font_size=6)
    for k in kinds:
        ax.scatter([], [], color=cmap(kinds.index(k) % 10), label=k)
    ax.legend(loc="upper left")
    ax.axis("off")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    print(f"graph image -> {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="free-text term or CURIE (e.g. MONDO:0007947)")
    ap.add_argument("--label", help="label for a CURIE input")
    ap.add_argument("-n", "--iterations", type=int, default=2)
    ap.add_argument("--limit", type=int, default=5, help="max edges per source per node")
    ap.add_argument("--max-frontier", type=int, default=50, help="max inputs per iteration")
    ap.add_argument("--sources", nargs="*", help="only use these source modules")
    ap.add_argument("-o", "--out", type=Path, default=Path("graph.html"),
                    help="output: .html (interactive viewer), .png or .graphml")
    args = ap.parse_args()

    sources = load_sources()
    if args.sources:
        sources = [s for s in sources if s.name in args.sources]
    print("sources:", ", ".join(s.name for s in sources) or "(none)")

    start = parse_input(args.input, args.label)
    edges = run(start, sources, args.iterations, args.limit, args.max_frontier)

    g = build_graph(edges)
    print(f"\n=== Graph: {g.number_of_nodes()} nodes, {g.number_of_edges()} edges ===")
    for key, d in g.nodes(data=True):
        print(f"  [{d['kind']}] {d['label']} ({key}) <{', '.join(sorted(d['sources']))}>  degree={g.degree(key)}")
    if not g.number_of_nodes():
        return
    if args.out.suffix == ".html":
        write_html(g, start, args.out)
    elif args.out.suffix == ".graphml":
        for _, d in g.nodes(data=True):  # graphml can't store sets
            d["sources"], d["xrefs"] = ",".join(sorted(d["sources"])), ",".join(sorted(d["xrefs"]))
        nx.write_graphml(g, args.out)
        print(f"graph -> {args.out}")
    else:
        draw(g, args.out)


if __name__ == "__main__":
    main()
