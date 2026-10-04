#!/usr/bin/env python3
"""Expand one medical input term into a graph of related nodes across datasets.

Each iteration runs every node from the previous iteration (the frontier) through
the sources in sources/ that accept it (Source.id_prefixes / Source.by_name: ID nodes
go only to sources that understand one of their ids, only the free-text input is
name-searched), collecting the connected nodes as the next frontier.

Nodes are deduplicated as they arrive (entities.py): nodes sharing an id/xref are one
entity, each source is queried at most once per entity, and repeated edges collapse.

The input is read by resolve.py. A disease name or CURIE is expanded as above. Symptoms,
HP ids and genes (one, or several separated by commas, "FBN1, ectopia lentis") instead
start a candidate search: diseases are ranked by how well their HPO annotations cover the
symptoms and whether they are linked to the genes (sources/_hpoa.py), the top
--candidates diseases are expanded as above and the top --focus-candidates of them get a
full profile. --as disease / candidates overrides the automatic reading, --rank-only
stops after the ranking; --symptoms is the list form of the candidate search.
symptom_anchor (improvements.py, --improvements): phenotype readings win under --symptoms,
the ranking is deduplicated per disease, only candidates close to the top (>= 2/3 of its
fully matched symptoms, >= 0.85 of its score) join the focus, the focus is deduplicated
after entity merges, and the anchor is printed.

Besides biomedical relations, the group sources (orphanet_groups, ern, eurordis, rdcrn,
clinicaltrials, genetic_alliance_uk/_us, nord) link a disease to who works on it: patient
organisations, expert centres, ERNs, research networks/consortia/projects, registries,
trials and their sponsors. ERN nodes are expanded to their member hospitals and ePAG
patient organisations in the next iteration (use -n 3 to get those for a free-text input).

Usage:
  python src/query-test/main.py "Marfan syndrome"
  python src/query-test/main.py "Marfan syndrome" -n 3 --limit 5 --sources mondo hpo
  python src/query-test/main.py MONDO:0007947 --label "Marfan syndrome" -o graph.png
  python src/query-test/main.py "tall stature, arachnodactyly, ectopia lentis" -o run.md
  python src/query-test/main.py FBN1 -o runs/fbn1.json         # gene -> its diseases
  python src/query-test/main.py "PMM2, cerebellar hypoplasia" --rank-only
  python src/query-test/main.py --symptoms "tall stature" arachnodactyly HP:0001083 -o run.md
  python src/query-test/main.py "Marfan syndrome" -o runs/marfan.json   # -> literature/main.py

Besides ids, sources attach what else they know to nodes (Node.info: descriptions,
synonyms, urls, cited papers) and to edges (Edge.evidence: PMIDs backing the association).
The .json output keeps all of it; the literature pipeline (literature/main.py) reads it.
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

import improvements
import quality
import resolve
from entities import LISTED, PRIORITY, Entities
from report import Stats, write_report
from sources import Edge, Node, _hpoa, load_sources

CURIE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*:\S+$")


def parse_input(text: str, label: str | None) -> Node:
    if CURIE.match(text):
        return Node(label=label or text, id=text, kind="unknown")
    return Node(label=text, kind="term")


def candidate_seed(interp, top: int, stats: Stats,
                   focus_candidates: int = 3) -> tuple[Node, list[Edge]]:
    """Rank the diseases the symptoms / genes of the input point to (resolve.rank,
    sources/_hpoa.py). Returns the query node and seed edges: query -> symptoms and genes,
    symptom -> disease (full matches), gene -> disease and query -> each ranked candidate.
    symptom_anchor: stats.focus_keys = the candidates resolve.focus_gate lets into the
    focus (at most focus_candidates), and the anchor is printed."""
    hpo = _hpoa.load()
    start = Node(interp.text, kind="query")
    print("input read as:")
    for p in interp.parts:
        print(f"  {p.text!r} -> " + (
            f"{p.kind} {p.label} ({p.id}, {p.how})" + (f"; {p.alternative}" if p.alternative else "")
            if p.kind != "unknown" else "NOT FOUND; closest HPO terms: "
            + "; ".join(f"{name} ({hp})" for hp, name in p.suggestions)))
    ranking = resolve.rank(interp, top) if interp.mode == "candidates" else []
    if ranking and interp.of("phenotype") and improvements.on("llm_rerank"):
        import rerank
        ranking = rerank.rerank(resolve.rank(interp, max(top, rerank.N)),
                                [p.id for p in interp.of("phenotype")],
                                [p.id for p in interp.of("gene")], hpo)[:top]
        moved = [r for r in ranking if r.get("tool_rank") not in (None, ranking.index(r) + 1)]
        if moved:
            print(f"(order of the top {rerank.N} revised by the language model; "
                  "llm_rerank, tool rank in brackets)")
    stats.candidates = (interp, ranking)
    seed: list[Edge] = []
    phen = {p.id: Node(p.label, id=p.id, kind="phenotype", source="hpoa")
            for p in interp.of("phenotype")}
    seed += [Edge(start, n, "has_symptom", "hpoa") for n in phen.values()]
    ncbi = {sym: gid for gid, sym in hpo.gene_ids.items()}
    genes = {p.id: Node(p.id, id=ncbi.get(p.id, f"SYMBOL:{p.id}"), kind="gene", source="hpoa",
                        xrefs=(f"SYMBOL:{p.id}",)) for p in interp.of("gene")}
    seed += [Edge(start, n, "has_gene", "hpoa") for n in genes.values()]
    print("ranked diseases:")
    for r in ranking:
        ids = [r["id"], *r["xrefs"]]
        orpha = [o for o in resolve.orphanet_ids(ids, r["name"]) if o not in ids]
        dis = Node(r["name"], id=r["id"], kind="disease", source="hpoa",
                   xrefs=tuple(r["xrefs"] + orpha))
        seed.append(Edge(start, dis, "candidate_disease", "hpoa"))
        seed += [Edge(phen[q], dis, "phenotype_of", "hpoa") for q, _, full, _ in r["matches"]
                 if full]
        for sym, assoc in dict(r["genes"]).items():
            rel = "causes_disease" if _hpoa.causal(assoc) else "gene_associated"
            seed.append(Edge(genes[sym], dis, rel, "hpoa"))
        full = sum(m[2] for m in r["matches"])
        why = [f"{full}/{len(phen)} symptoms" if phen else "",
               ", ".join(sorted(dict(r["genes"]))), "named in the input" if r.get("pinned") else ""]
        print(f"  {r['score']:5.1f}  {r['name']} ({r['id']})  " + "; ".join(w for w in why if w)
              + (f" [{r['tool_rank']}]" if r.get("tool_rank") else ""))
        r["_key"] = dis.key()
    if improvements.on("symptom_anchor") and ranking:
        chosen = resolve.focus_gate(ranking, focus_candidates, len(phen))
        stats.focus_keys = [r["_key"] for r in chosen]
        top = chosen[0]
        full = sum(m[2] for m in top["matches"])
        print(f"\nANCHOR (the evidence graph is built for it): {top['name']} ({top['id']})"
              + (f", {full}/{len(phen)} symptoms fully matched" if phen else "")
              + (", named in the input" if top.get("pinned") else ""))
        if len(chosen) > 1:
            print("further focus candidates: " + "; ".join(
                f"{r['name']} ({r['id']})" for r in chosen[1:]))
        left = [r for r in ranking[:focus_candidates] if r not in chosen]
        if left:
            print("not profiled (too far below the anchor: < 2/3 of its symptoms or < 0.85 "
                  "of its score): " + "; ".join(f"{r['name']} ({r['id']})" for r in left))
    for r in ranking:
        r.pop("_key", None)
    return start, seed


def run(start: Node, sources, iterations: int, limit: int, max_frontier: int,
        ents: Entities, stats: Stats | None = None, focus_limit: int = 40,
        seed: list[Edge] | None = None, focus_candidates: int = 3) -> list[Edge]:
    """Expand `start` (or, for a symptom / gene input, the candidate diseases of the
    `seed` edges).

    The focus entities (the CURIE input, the best name match of a free-text input, or
    the top `focus_candidates` ranked diseases) are queried with `focus_limit` instead of `limit`,
    so their profile is complete enough to read. Noise (animal diseases, taxa, lab codes,
    model-organism phenotypes) and off-topic name matches are dropped and recorded in
    stats.dropped; the frontier is ordered by how many sources reached an entity, and
    generic nodes (inheritance modes, ...) are not expanded.
    """
    import time
    stats = stats or Stats()
    edges: list[Edge] = []
    if seed:
        ents.add(start)
        for e in seed:
            ents.add(e.src)
            ents.add(e.dst)
            stats.depth[id(e)] = 0
        edges += seed
        frontier = list(dict.fromkeys(ents.find(e.dst.key()) for e in seed
                                      if e.dst.kind == "disease"))
        stats.focus = frontier[:focus_candidates]
        if improvements.on("symptom_anchor") and stats.focus_keys is not None:
            stats.focus = list(dict.fromkeys(ents.find(k) for k in stats.focus_keys))
    else:
        frontier = [ents.add(start)]
        stats.focus = frontier[:] if start.id else []
    free_text = start.id is None and not seed
    # ids each source was asked with / that gave results: a source that found nothing for
    # an entity is asked again once a merge gives the entity ids it has not tried (symptom
    # candidates start with ORPHA/OMIM ids only; OpenTargets needs the MONDO id that
    # Monarch's answer merges in)
    tried: dict[str, set[str]] = {s.name: set() for s in sources}
    productive: dict[str, set[str]] = {s.name: set() for s in sources}

    def wanted(root: str, node: Node, src) -> bool:
        if not src.accepts(node):
            return False
        if src.name not in ents.queried(root):
            return True
        ids = set(src.ids_for(node))
        return not ids & productive[src.name] and bool(ids - tried[src.name])

    for i in range(1, iterations + 1):
        if len(frontier) > max_frontier:
            print(f"(frontier of {len(frontier)} capped to {max_frontier})")
            frontier = frontier[:max_frontier]
        focus = {ents.find(f) for f in stats.focus}
        found: list[Edge] = []
        for root in frontier:
            node = ents.node(root)  # merged view: all ids/xrefs known for this entity
            is_focus = ents.find(root) in focus
            for src in sources:
                if not wanted(root, node, src):
                    continue
                ents.mark_queried(root, src.name)
                lim = min(focus_limit, src.focus_cap or focus_limit) if is_focus else limit
                rec, t0 = stats.src[src.name], time.time()
                rec["calls"] += 1
                try:
                    got = src.query(node, limit=lim)
                except Exception as e:  # sources shouldn't raise, but don't let one kill the run
                    print(f"  ! {src.name} failed on {node.label}: {e}", file=sys.stderr)
                    rec["raised"] += 1
                    got = []
                rec["seconds"] += time.time() - t0
                rec["edges"] += len(got)
                rec["empty"] += not got
                stats.depth.update((id(e), i) for e in got)
                ids = set(src.ids_for(node))
                tried[src.name] |= ids
                if got:
                    productive[src.name] |= ids
                found += got
                if is_focus and improvements.on("name_focus") and\
                        any(e.relation == "xref" for e in got):
                    # ids the focus gains here (MONDO's ORPHA / OMIM / NORD xrefs) reach
                    # the sources still to come in this iteration, not just the next one
                    for e in got:
                        if e.relation == "xref":
                            ents.add(e.src, e.source)
                            ents.add(e.dst, e.source)
                            ents.merge_xref(e.src.key(), e.dst.key())
                    ents.merge_listed()
                    node = ents.node(root)
        print(f"\n=== Iteration {i}: {len(frontier)} inputs -> {len(found)} edges ===")
        for e in found:
            ents.add(e.src, e.source)  # sources may return the input with extra ids/info
            ents.add(e.dst, e.source)
            if e.relation == "xref":  # exact mapping: same entity
                ents.merge_xref(e.src.key(), e.dst.key())
        if improvements.on("name_focus"):  # NORD / GARD records an entity maps to
            ents.merge_listed()
        if improvements.on("symptom_anchor"):  # entities merged: one focus slot each
            stats.focus = list(dict.fromkeys(ents.find(f) for f in stats.focus))
        kept = _filter(found, start, ents, stats, name_hits=free_text and i == 1)
        for e in kept:
            print(f"  {e.src.label}  --{e.relation}-->  {e.dst}")
        edges += kept
        if free_text and i == 1:
            stats.focus = _best_match(kept, start, ents)
        # next: entities reached (or queried) this iteration that a source still wants to
        # see (not asked yet, or asked without result and since given new ids), focus
        # first, then most-corroborated; generic hubs are never expanded
        support: dict[str, set[str]] = {}
        for e in kept:
            support.setdefault(ents.find(e.dst.key()), set()).add(e.source)
        focus = {ents.find(f) for f in stats.focus}
        frontier = []
        for root in dict.fromkeys([*support, *(ents.find(r) for r in frontier)]):
            node = ents.node(root)
            if quality.is_generic(node) or quality.is_noise(node):
                continue
            if any(wanted(root, node, s) for s in sources):
                frontier.append(root)
        frontier.sort(key=lambda r: (r not in focus, -len(support.get(r, ()))))
        if not frontier:
            break
    return edges


def _filter(found: list[Edge], start: Node, ents: Entities, stats: Stats,
            name_hits: bool) -> list[Edge]:
    """Drop noise everywhere and, for the free-text input, name-search hits that share no
    word with it unless at least two sources agree on them (keeps true synonyms such as
    "Lou Gehrig disease" -> "amyotrophic lateral sclerosis")."""
    agree = _agreement(found, start, ents) if name_hits else {}
    kept = []
    for e in found:
        if quality.is_noise(e.dst):
            stats.dropped.append((e, "non-human / taxon / lab code / model organism"))
        elif (name_hits and e.relation == "matches" and e.src.key() == start.key()
              and not any(quality.similar(start.label, n)
                          for n in _names(ents, ents.find(e.dst.key()), e.dst.label))
              and len(agree[ents.find(e.dst.key())]) < 2):
            stats.dropped.append((e, "off-topic name match"))
        else:
            kept.append(e)
    return kept


def _agreement(edges: list[Edge], start: Node, ents: Entities) -> dict[str, set[str]]:
    """Entity -> the sources whose name search for the input returned it. name_focus:
    hits of different sources with the same label agree too, before their ids are merged
    (MONDO:0009290 and DOID:2752 are both "glycogen storage disease II")."""
    agree: dict[str, set[str]] = {}
    by_label: dict[str, set[str]] = {}
    labels: dict[str, set[str]] = {}
    for e in edges:
        if e.relation == "matches" and e.src.key() == start.key():
            r = ents.find(e.dst.key())
            agree.setdefault(r, set()).add(e.source)
            if improvements.on("name_focus"):
                lab = " ".join(re.findall(r"[a-z0-9]+", e.dst.label.lower()))
                by_label.setdefault(lab, set()).add(e.source)
                labels.setdefault(r, set()).add(lab)
    for r, labs in labels.items():
        for lab in labs:
            agree[r] |= by_label[lab]
    return agree


def _names(ents: Entities, root: str, label: str) -> list[str]:
    """A hit's label and, under name_focus, the exact synonyms its source gave."""
    if not improvements.on("name_focus"):
        return [label]
    return [label, *(ents.record(root)["info"].get("synonyms") or ())]


def _best_match(kept: list[Edge], start: Node, ents: Entities) -> list[str]:
    """The entity the free-text input means: a hit whose label equals the input, the
    one most sources returned; else the hit most sources agree on.
    name_focus: an exact synonym counts as an exact label; a one-source record of a
    disease directory (NORD, GARD: no xrefs, no relations) is not preferred for its
    label alone; ties go to the better id prefix (MONDO before ORPHA ... NORD)."""
    agree = _agreement(kept, start, ents)
    if not agree:
        return []
    want = start.label.strip().lower()
    if not improvements.on("name_focus"):
        exact = {e_root for e_root in agree
                 if (ents.record(e_root)["label"] or "").strip().lower() == want}
        return [max(agree, key=lambda r: (r in exact, len(agree[r])))]
    rank = {p: i for i, p in enumerate(PRIORITY)}

    def key(r):
        rec = ents.record(r)
        names = {n.strip().lower() for n in _names(ents, r, rec["label"] or "")}
        thin = len(agree[r]) < 2 and all(i.split(":", 1)[0] in LISTED for i in rec["ids"])
        return (want in names and not thin, len(agree[r]),
                -rank.get(ents.key(r).split(":", 1)[0], len(PRIORITY)))
    return [max(agree, key=key)]


def build_graph(edges: list[Edge], ents: Entities) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for e in edges:
        u, v = (ents.key(ents.find(n.key())) for n in (e.src, e.dst))
        if u == v:  # e.g. an xref edge between two ids of one merged entity
            continue
        for k in (u, v):
            if k not in g:
                r = ents.record(k)
                g.add_node(k, label=r["label"], kind=r["kind"], sources=set(r["sources"]),
                           xrefs=(r["ids"] | r["xrefs"]) - {k}, info=r["info"])
        # keyed by relation+source: the same fact reported twice by one source is one edge,
        # the same fact from different sources stays visible as parallel edges
        key = f"{e.relation}|{e.source}"
        old = g.get_edge_data(u, v, key)
        evidence = list(dict.fromkeys([*(old["evidence"] if old else ()), *e.evidence]))
        g.add_edge(u, v, key=key, relation=e.relation, source=e.source, evidence=evidence)
    return g


def write_json(g: nx.MultiDiGraph, start_key: str, focus: list[str], args, path: Path,
               query: dict | None = None):
    """The whole graph with node info and edge evidence: input of literature/main.py.
    `query`: for a symptom / gene input, how it was read and the ranked candidates
    (present.py writes its candidate overview from it)."""
    import json
    data = {
        "start": start_key,
        "focus": focus,
        "args": {k: (str(v) if isinstance(v, Path) else
                     [str(x) for x in v] if k == "out" else v) for k, v in vars(args).items()},
        "query": query,
        "nodes": [{"id": k, "label": d["label"], "kind": d["kind"],
                   "sources": sorted(d["sources"]), "xrefs": sorted(d["xrefs"]),
                   "info": d["info"]} for k, d in g.nodes(data=True)],
        "edges": [{"from": u, "to": v, "relation": d["relation"], "source": d["source"],
                   "evidence": d["evidence"]} for u, v, d in g.edges(data=True)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=sorted),
                    encoding="utf-8")
    print(f"graph json -> {path.resolve()}")


def _query_json(stats: Stats, ents: Entities) -> dict | None:
    """How a symptom / gene input was read and its ranked candidates, with each candidate's
    graph node id."""
    if not stats.candidates:
        return None
    interp, ranking = stats.candidates
    hpo = _hpoa.load()

    def node_id(r):
        try:
            return ents.key(ents.find(r["id"]))
        except KeyError:
            return r["id"]
    return {"text": interp.text, "mode": interp.mode,
            "parts": [p.as_dict() for p in interp.parts],
            "ranking": [{"node": node_id(r), "id": r["id"], "name": r["name"],
                         "xrefs": r["xrefs"], "score": round(r["score"], 2),
                         "pinned": bool(r.get("pinned")),
                         "genes": [{"symbol": g, "association": a} for g, a in r["genes"]],
                         "matches": [{"query": q, "query_label": hpo.name.get(q, q),
                                      "matched": t, "matched_label": hpo.name.get(t, t),
                                      "full": full, "frequency": round(f, 2)}
                                     for q, t, full, f in r["matches"]]}
                        for r in ranking]}


def write_html(g: nx.MultiDiGraph, start_key: str, path: Path):
    """Embed the graph as JSON into viewer.html (interactive vis-network page)."""
    import json
    data = {
        "start": start_key,
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
    ap.add_argument("input", nargs="?",
                    help="what to search: a disease name or CURIE (MONDO:0007947), or symptoms "
                         "/ genes / HP ids, comma-separated ('FBN1, ectopia lentis'); "
                         "see resolve.py for how it is read")
    ap.add_argument("--symptoms", nargs="+", metavar="SYMPTOM",
                    help="list input: every argument (or comma-separated piece) is a symptom, "
                         "HP id or gene; same as --as candidates")
    ap.add_argument("--as", dest="read_as", choices=("auto", "disease", "candidates"),
                    default="auto",
                    help="disease: name-search the input as it is; candidates: read it as "
                         "symptoms / genes even where a part is also a disease name "
                         "('microcephaly'); auto (default): decide per input")
    ap.add_argument("--candidates", type=int, default=10,
                    help="symptom / gene input: how many ranked diseases to expand")
    ap.add_argument("--focus-candidates", type=int, default=3,
                    help="symptom / gene input: how many of the top candidates get a full "
                         "profile (queried with --focus-limit)")
    ap.add_argument("--rank-only", action="store_true",
                    help="only show how the input is read and the ranked candidates")
    ap.add_argument("--label", help="label for a CURIE input")
    ap.add_argument("-n", "--iterations", type=int, default=2)
    ap.add_argument("--limit", type=int, default=5, help="max edges per source per node")
    ap.add_argument("--focus-limit", type=int, default=40,
                    help="max edges per source for the focus entity (the disease the query is about)")
    ap.add_argument("--max-frontier", type=int, default=50, help="max inputs per iteration")
    ap.add_argument("--sources", nargs="*", help="only use these source modules")
    ap.add_argument("-o", "--out", type=Path, action="append",
                    help="output (repeatable: -o run.json -o run.md): .html (interactive "
                         "viewer, default graph.html), .md (readable report), .json (graph with "
                         "node info + edge evidence, input of present.py and "
                         "literature/main.py), .png or .graphml")
    improvements.add_argument(ap)
    args = ap.parse_args()
    improvements.apply_args(args)  # effective SPEC lands in the graph JSON's "args"
    # labels contain characters such as "≥"; a redirected stdout on Windows is cp1252
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    if not args.input and not args.symptoms:
        ap.error("give an input (disease, symptoms, genes) or --symptoms")
    args.out = args.out or [Path("graph.html")]

    sources = load_sources()
    if args.sources:
        sources = [s for s in sources if s.name in args.sources]
    print("sources:", ", ".join(s.name for s in sources) or "(none)")

    ents = Entities()
    stats = Stats()
    for s in sources:
        stats.instrument(s)
    seed = None
    hpo = _hpoa.load()
    if hpo is None:
        interp = None
        if args.symptoms or args.read_as == "candidates":
            ap.error("symptom / gene search needs the local HPO files (download failed)")
    elif args.symptoms or args.read_as == "candidates":
        interp = resolve.interpret_list(args.symptoms or [args.input], hpo,
                                        prefer_phenotype=True)
    elif args.read_as == "disease":
        interp = None
    else:
        interp = resolve.interpret(args.input, hpo)
    if interp is not None and interp.mode != "disease":
        start, seed = candidate_seed(interp, args.candidates, stats, args.focus_candidates)
        if not seed or args.rank_only:
            if interp.mode == "none":
                print("nothing in the input was recognised as a disease, symptom or gene; "
                      "pass one of the closest HPO terms above (name or HP id)")
            return
    else:
        start = parse_input(args.input, args.label)
        p = interp.parts[0] if interp is not None else None
        print(f"input read as: disease {args.input!r}" + (
            "" if p and p.how == "id" else
            f" (= {p.label}, {p.id})" if p and p.kind == "disease" else
            " (not a known symptom or gene: name search)"))
        if p and p.kind == "disease" and p.alternative:
            print(f"note: '{p.text}' is read as the disease {p.label} ({p.alternative}); "
                  f"use --as candidates to find diseases that have it")
        if args.rank_only:
            return
    edges = run(start, sources, args.iterations, args.limit, args.max_frontier, ents, stats,
                focus_limit=args.focus_limit, seed=seed,
                focus_candidates=args.focus_candidates)

    g = build_graph(edges, ents)
    print(f"\n=== Graph: {g.number_of_nodes()} nodes, {g.number_of_edges()} edges ===")
    for key, d in g.nodes(data=True):
        print(f"  [{d['kind']}] {d['label']} ({key}) <{', '.join(sorted(d['sources']))}>  degree={g.degree(key)}")
    if not g.number_of_nodes():
        return
    # graphml flattens the node data in place: written last
    for out in sorted(args.out, key=lambda o: o.suffix == ".graphml"):
        if out.suffix == ".md":
            write_report(g, edges, ents, start, stats, args, out)
        elif out.suffix == ".html":
            write_html(g, ents.key(ents.find(start.key())), out)
        elif out.suffix == ".json":
            focus = [ents.key(ents.find(f)) for f in stats.focus]
            if improvements.on("symptom_anchor"):
                focus = list(dict.fromkeys(focus))
            write_json(g, ents.key(ents.find(start.key())), focus, args, out,
                       query=_query_json(stats, ents))
        elif out.suffix == ".graphml":
            import json
            for _, d in g.nodes(data=True):  # graphml can't store sets / dicts
                d["sources"], d["xrefs"] = ",".join(sorted(d["sources"])), ",".join(sorted(d["xrefs"]))
                d["info"] = json.dumps(d["info"], ensure_ascii=False)
            for *_, d in g.edges(data=True):
                d["evidence"] = ",".join(d["evidence"])
            nx.write_graphml(g, out)
            print(f"graph -> {out}")
        else:
            draw(g, out)


if __name__ == "__main__":
    main()
