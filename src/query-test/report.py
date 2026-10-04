"""Readable Markdown report of a run (main.py -o run.md), meant for reviewing data quality.

Sections: per-source stats (calls, edges, time, HTTP failures), how the input term was
resolved (or, for a symptom / gene input, how it was read and the disease ranking), the
profile of the
focus entity (its neighbours grouped by relation, with the sources that agree on each),
convergence hubs (nodes linking several diseases, weighted by how specific they are),
what the filters dropped, and automatic quality warnings (placeholder labels,
obsolete terms, kind mismatches).
"""
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
import quality
from entities import IDENTITY
from sources import Edge

# relation groups for the entity profile; anything else lands in "other"
GROUPS = [
    ("Diseases with this phenotype", ()),  # phenotype links seen from a phenotype focus
    ("Genes", ("gene", "target")),
    ("Phenotypes / symptoms", ("phenotype", "inheritance")),
    ("Drugs / treatments", ("treat", "indicat", "drug", "contraindic", "off_label", "side_effect")),
    ("Variants", ("variant",)),
    ("Hierarchy", ("subclass", "parent", "therapeutic_area")),
    ("Cross-references", ("xref", "matches")),
    ("Anatomy / expression", ("location", "expressed", "tissue", "anatomy")),
    ("Groups / organisations / research", ("organisation", "expert_", "network", "consortium",
                                           "research_", "trial", "registry", "biobank",
                                           "sponsored", "collaborator", "investigator",
                                           "ern_")),
]


class Stats:
    """Per-source counters filled in by main.run() (instrument() wraps each session)."""

    def __init__(self):
        self.src = defaultdict(lambda: {"calls": 0, "edges": 0, "empty": 0, "raised": 0,
                                        "seconds": 0.0, "http": Counter(), "http_errors": []})
        self.depth: dict[int, int] = {}  # id(edge) -> iteration it was found in
        self.focus: list[str] = []  # entity roots the query is about (main.run)
        self.dropped: list[tuple[Edge, str]] = []  # edges removed by main._filter
        self.candidates = None  # symptom / gene input: (resolve.Interpretation, ranking)
        self.focus_keys: list[str] | None = None  # symptom_anchor: gated focus candidates
        self.started = time.time()

    def instrument(self, src):
        """Record HTTP status codes / exceptions of a source's requests.Session (sources
        swallow errors and return [], so without this a failure looks like 'no data')."""
        sess = getattr(src, "session", None)
        if sess is None:
            return
        orig, rec = sess.request, self.src[src.name]

        def request(method, url, *a, **kw):
            try:
                r = orig(method, url, *a, **kw)
            except Exception as e:
                rec["http"]["exc"] += 1
                rec["http_errors"].append(f"{type(e).__name__} {url[:90]}")
                raise
            rec["http"][r.status_code] += 1
            if r.status_code >= 400:
                rec["http_errors"].append(f"{r.status_code} {url[:90]}")
            return r
        sess.request = request


def _stage(rels: set[str]) -> int:
    """Sort key of a drug's relations: approved < phase 4 < 3 < 2 < 1 < other."""
    best = 9
    for r in rels:
        r = r.removeprefix("(inverse) ")
        if r in ("approved_drug", "treated_by", "indicated_for"):
            best = min(best, 0)
        elif r.startswith("trial_drug_phase_"):
            best = min(best, 5 - int(r.removeprefix("trial_drug_phase_")[0]))
    return best


def _group(rel: str, other_kind: str) -> str:
    if other_kind == "disease" and "phenotype" in rel:
        return "Diseases with this phenotype"
    for name, keys in GROUPS:
        if any(k in rel for k in keys):
            return name
    return "Other"


def write_report(g: nx.MultiDiGraph, edges: list[Edge], ents, start, stats: Stats,
                 args, path: Path, top: int = 25):
    key = lambda n: ents.key(ents.find(n.key()))  # noqa: E731
    label = lambda k: g.nodes[k]["label"] if k in g else k  # noqa: E731
    out: list[str] = []
    w = out.append
    start_key = key(start)

    w(f"# Query report: {start.label}\n")
    w(f"- settings: iterations={args.iterations}, limit={args.limit}, "
      f"max_frontier={args.max_frontier}")
    w(f"- runtime: {time.time() - stats.started:.0f}s; graph: {g.number_of_nodes()} nodes, "
      f"{g.number_of_edges()} edges (raw edges: {len(edges)})")
    kinds = Counter(d["kind"] for _, d in g.nodes(data=True))
    w("- node kinds: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common()) + "\n")

    # -- sources --------------------------------------------------------
    w("## Sources\n")
    w("| source | calls | edges | empty | time s | HTTP | failures |")
    w("|---|---|---|---|---|---|---|")
    for name, r in sorted(stats.src.items(), key=lambda kv: -kv[1]["edges"]):
        http = " ".join(f"{c}x{n}" for c, n in sorted(r["http"].items(), key=str))
        fail = "; ".join(dict.fromkeys(r["http_errors"]))[:160]
        w(f"| {name} | {r['calls']} | {r['edges']} | {r['empty']} | {r['seconds']:.1f} | "
          f"{http} | {fail}{' raised ' + str(r['raised']) if r['raised'] else ''} |")
    w("")

    # -- input resolution -------------------------------------------------
    first = [e for e in edges if stats.depth.get(id(e)) == 1 and start.id is None
             and e.src.key() == start.key() and e.relation == "matches"]
    off = [e for e, why in stats.dropped if why == "off-topic name match"]
    if first or off:
        w("## Input resolution (iteration 1)\n")
        by_ent: dict[str, list] = defaultdict(list)
        for e in first:
            by_ent[key(e.dst)].append(e)
        for k, es in sorted(by_ent.items(), key=lambda kv: -len(kv[1])):
            srcs = sorted({e.source for e in es})
            w(f"- {label(k)} `{k}` [{g.nodes[k]['kind'] if k in g else '?'}] <{', '.join(srcs)}>")
        if off:
            w("\nDropped as off-topic (no shared word, only one source):\n")
            for e in off:
                w(f"- ~~{e.dst.label}~~ `{e.dst.id}` <{e.source}>")
        w("")

    # -- candidate search (symptom / gene input) -------------------------
    if stats.candidates:
        interp, ranking = stats.candidates
        w("## Candidate search\n")
        w("How the input was read (resolve.py):\n")
        for p in interp.parts:
            if p.kind == "unknown":
                w(f"- '{p.text}' → **not recognised**, not used. Closest HPO terms (pass one "
                  "as a name or HP id to use it): "
                  + "; ".join(f"{name} `{hp}`" for hp, name in p.suggestions))
            else:
                w(f"- '{p.text}' → {p.kind} {p.label} `{p.id}` ({p.how})"
                  + (f" — {p.alternative}" if p.alternative else ""))
        n = len(interp.of("phenotype"))
        w("\nDiseases ranked: causally linked to a searched gene first, then other gene "
          "links, then by how well their HPO annotations cover the symptoms (specific "
          "symptoms weigh more; ✓ = has the symptom or a more specific one, ~ = only a "
          "related broader term; % = annotated frequency).\n")
        w("| # | disease | genes | score | symptoms |")
        w("|---|---|---|---|---|")
        names = {p.id: p.label for p in interp.of("phenotype")}
        hpo = quality._hpoa.load()
        for i, r in enumerate(ranking, 1):
            ms = []
            for q, t, full, f in r["matches"]:
                via = "" if full else f" (via {hpo.name.get(t, t) if hpo else t})"
                ms.append(f"{'✓' if full else '~'} {names.get(q, q)}{via} {f:.0%}")
            ids = ", ".join([r["id"], *r["xrefs"]])
            genes = "; ".join(f"{g} ({a.lower()})" for g, a in dict(r["genes"]).items())
            pin = " (named in the input)" if r.get("pinned") else ""
            sym = f"{sum(m[2] for m in r['matches'])}/{n}: {'; '.join(ms)}" if n else ""
            w(f"| {i} | {r['name']} `{ids}`{pin} | {genes} | {r['score']:.1f} | {sym} |")
        w("")

    # -- focus entity profile ------------------------------------------------
    main = ents.key(ents.find(stats.focus[0])) if stats.focus else start_key
    if main in g:
        d = g.nodes[main]
        w(f"## Profile of focus entity: {d['label']} `{main}`\n")
        w(f"- sources: {', '.join(sorted(d['sources']))}")
        w(f"- ids/xrefs: {', '.join(sorted(d['xrefs']))[:600]}")
        info = d.get("info") or {}
        descs = info.get("descriptions") or {}
        if descs:
            src, text = next(iter(descs.items()))
            w(f"- description <{src}> (of {len(descs)}): {text[:400]}")
        if info.get("synonyms"):
            w(f"- synonyms: {'; '.join(info['synonyms'][:12])}")
        cited = {r for *_, ev in g.edges(main, data="evidence") for r in ev or ()}
        cited |= {r for _, _, ev in g.in_edges(main, data="evidence") for r in ev or ()}
        w(f"- papers: {len(info.get('refs') or [])} cited for the entity, {len(cited)} as "
          f"evidence of its edges (literature/main.py collects them)\n")
        groups: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: (set(), set())))
        for u, v, ed in g.edges(data=True):
            if main not in (u, v):
                continue
            other = v if u == main else u
            # the synthetic query node (free-text term / symptom list) is not a fact about
            # the entity; with a CURIE input the start *is* the entity, so nothing is skipped
            if other == start_key:
                continue
            rel = ed["relation"] if u == main else f"(inverse) {ed['relation']}"
            rels, srcs = groups[_group(ed["relation"], g.nodes[other]["kind"])][other]
            rels.add(rel)
            srcs.add(ed["source"])
        for gname, _ in [*GROUPS, ("Other", ())]:
            items = groups.get(gname)
            if not items:
                continue
            w(f"### {gname} ({len(items)})\n")
            # drugs: approved first, then by trial phase; otherwise most-agreed first
            for other, (rels, srcs) in sorted(items.items(), key=lambda kv: (
                    _stage(kv[1][0]) if gname.startswith("Drugs") else 0,
                    -len(kv[1][1]), label(kv[0]))):
                w(f"- {label(other)} `{other}` — {', '.join(sorted(rels))} "
                  f"<{', '.join(sorted(srcs))}> ×{len(srcs)}")
            w("")

    # -- convergence hubs ------------------------------------------------------
    w("## Convergence hubs\n")
    w("Nodes that link several of the diseases found (excluding the focus entity), "
      "ranked by linked diseases × specificity. Specificity is 0..1: HPO information "
      "content for phenotypes, PrimeKG disease/phenotype degree otherwise, 0 for "
      "inheritance modes, so generic nodes such as 'Autosomal dominant inheritance' sink.\n")
    hubs = []
    for n, d in g.nodes(data=True):
        if n in (main, start_key):
            continue
        nb = {v for _, v, r in g.out_edges(n, data="relation") if not r.startswith("xref")}
        nb |= {u for u, _, r in g.in_edges(n, data="relation") if not r.startswith("xref")}
        nb -= {n, main, start_key}
        dis = {x for x in nb if g.nodes[x]["kind"] == "disease" and g.nodes[x]["label"] != x}
        if len(dis) < 2:
            continue
        spec = quality.specificity(n, set(d["xrefs"]), d["label"], d["kind"])
        hubs.append((len(dis) * spec, len(dis), spec, n, d, dis))
    hubs.sort(key=lambda t: (-t[0], t[4]["label"]))
    w("| node | kind | linked diseases | specificity | score | examples |")
    w("|---|---|---|---|---|---|")
    for score, cnt, spec, n, d, dis in hubs[:top]:
        ex = "; ".join(sorted(label(x) for x in dis)[:4])[:140]
        w(f"| {d['label']} `{n}` | {d['kind']} | {cnt} | {spec:.2f} | {score:.1f} | {ex} |")
    if not hubs:
        w("| (no node links two or more diseases) | | | | | |")
    w("")

    # -- filtered out ------------------------------------------------------------
    if stats.dropped:
        w("## Filtered out\n")
        by_why: dict[str, list[str]] = defaultdict(list)
        for e, why in stats.dropped:
            by_why[why].append(f"{e.dst.label} `{e.dst.id}` <{e.source}>")
        for why, items in by_why.items():
            items = list(dict.fromkeys(items))
            w(f"- **{why}** ({len(items)}): " + "; ".join(items[:12])
              + (" …" if len(items) > 12 else ""))
        w("")

    # -- quality warnings -------------------------------------------------------
    w("## Quality warnings\n")
    warns: dict[str, list[str]] = defaultdict(list)
    for n, d in g.nodes(data=True):
        lab = d["label"]
        # ICD/UMLS/... xrefs are expected to be bare codes; only ids we query should have names
        if (lab == n or lab in d["xrefs"]) and n.split(":")[0] in IDENTITY:
            warns["Unresolved placeholder label (only an id)"].append(f"`{n}`")
        if re.search(r"non-human|NCBITaxon", lab + n, re.I):
            warns["Non-human / taxon node (should have been filtered)"].append(f"{lab} `{n}`")
        if lab.lower().startswith("obsolete"):
            warns["Obsolete term"].append(f"{lab} `{n}`")
        if d["kind"] == "gene" and n.split(":")[0] not in ("HGNC", "NCBIGene", "ENSEMBL", "SYMBOL"):
            warns["Gene node with non-gene id"].append(f"{lab} `{n}`")
        if d["kind"] in ("term", "unknown"):
            warns["Untyped node (kind term/unknown)"].append(f"{lab} `{n}`")
    for e in edges:
        if key(e.src) == key(e.dst) and e.relation not in ("xref", "matches"):
            warns["Self-loop after merging (dropped from graph)"].append(
                f"{e.src.label} --{e.relation}--> {e.dst.label} `{e.dst.id}` <{e.source}>")
    for title, items in warns.items():
        items = list(dict.fromkeys(items))
        w(f"### {title} ({len(items)})\n")
        for it in items[:top]:
            w(f"- {it}")
        if len(items) > top:
            w(f"- … {len(items) - top} more")
        w("")
    if not warns:
        w("none\n")

    path.write_text("\n".join(out), encoding="utf-8")
    print(f"report -> {path.resolve()}")
