#!/usr/bin/env python3
"""Markdown context of the webapp's graph chat, written from the exported graph views
(web_export.py: <stem>.present.json, <stem>.evidence.json) next to them as .md.

Unlike the review reports (present.py / evidence/main.py write_md), the context covers
the whole graph: every part of it (research gaps, candidates, evidence edges, papers,
entities; or every topic and entry of the overview) with as much detail as a character
budget allows. Each part is ranked by importance and gets a share of the budget; what a
part leaves unused goes to the next, and lower-ranked rows are shortened before they are
dropped, so the model sees the most important data in full and the rest at least named.

Usage:
  python src/query-test/chat_context.py webapp/public/graph-data/pompe.evidence.json
  python src/query-test/chat_context.py runs/x.present.json -o x.md --budget 150000
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

BUDGET = 240_000  # characters, about 60k tokens; the webapp caps the context a little above
FULL_SHARE = .6  # of a section's budget for its top rows in full, the rest names the others
LEVEL_RANK = {"clinical_trial": 9, "registered_trial": 8, "observational": 7, "case_report": 6,
              "animal": 5, "in_vitro": 4, "in_silico": 3, "review": 2, "inferred": 1}


def _cut(text, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


class Writer:
    """Collects sections within a total budget; a section's unused share rolls over."""

    def __init__(self, budget: int):
        self.budget, self.out, self.used, self.carry = budget, [], 0, 0

    def add(self, lines: list[str]):
        for line in lines:
            self.out.append(line)
            self.used += len(line) + 1

    def section(self, title: str, rows: list[tuple[str, str]], share: float, intro: str = ""):
        """rows: (full, short) renderings, most important first. The top rows in full
        (up to FULL_SHARE of the section's budget, or all of it when everything fits),
        the following ones short, then a count of what was left out."""
        if not rows:
            return
        budget = int(self.budget * share) + self.carry
        head = [f"## {title}", ""] + ([intro, ""] if intro else [])
        used, lines, shown = sum(len(x) + 1 for x in head), [], 0
        full_budget = budget if sum(len(f) + 1 for f, _ in rows) + used <= budget \
            else used + FULL_SHARE * (budget - used)
        for full, short in rows:
            text = full if used + len(full) + 1 <= full_budget else short
            if used + len(text) + 1 > budget:
                break
            full_budget = full_budget if text is full else 0  # once short, the rest stays short
            lines.append(text)
            used += len(text) + 1
            shown += 1
        if shown < len(rows):
            lines.append(f"- … {len(rows) - shown} lower-ranked entries not included")
            used += 60
        self.carry = max(0, budget - used)
        self.add(head + lines + [""])

    def text(self) -> str:
        return "\n".join(self.out) + "\n"


# -- evidence graph ------------------------------------------------------------------------
def evidence_md(view: dict, budget: int = BUDGET) -> str:
    nodes = {n["id"]: n for n in view["nodes"]}
    label = lambda i: nodes[i]["label"] if i in nodes else i  # noqa: E731
    papers = {p["key"]: p for p in view["papers"]}
    st, sc = view.get("stats") or {}, view.get("screening") or {}
    w = Writer(budget)
    w.add([f"# Evidence graph: existing solutions for {view['disease']} ({view['start']})", "",
           f"Papers screened {st.get('screened', '?')}, included {st.get('included', '?')}, read in full "
           f"or abstract {st.get('read', '?')} · {len(view['nodes'])} entities · {len(view['edges'])} "
           f"evidence edges · {len(view['candidates'])} candidate solutions · {len(view['gaps'])} research "
           f"gaps · extraction model {(view.get('settings') or {}).get('model', '?')}",
           *([f"Screened papers by connection to the disease: "
              + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sc["by_connection"].items())]
             if sc.get("by_connection") else []),
           "",
           "Reading guide: a candidate is *direct* when it was tested in the disease, its patients' cells "
           "or its models (or is registered for it), *transfer* when it comes from a related disease or "
           "shared mechanism. Scores (0-1) combine evidence level, number of papers and how the candidate "
           "connects to the disease. Evidence levels, strongest first: "
           + ", ".join(LEVEL_RANK) + ". Quotes are verbatim from the papers (paper id, passage).", ""])

    gaps = sorted(view["gaps"], key=lambda g: (g["status"] == "tested", -(g.get("plausibility") or 0),
                                               -(g.get("score") or 0)))
    w.section("Research gaps", [(
        "\n".join(x for x in [
            f"- **{g['intervention_label']}** → {g['mechanism_label']} — {g['status']}"
            f"{f', plausibility {g['plausibility']}/3' if g.get('plausibility') is not None else ''}"
            f", evidence {g.get('level') or '?'}, {(g.get('literature') or {}).get('count') or 0} papers "
            f"on it in the disease, {len(g.get('trials') or [])} trials",
            f"  - edge: {g['intervention_edge']}" if g.get("intervention_edge") else "",
            f"  - rationale: {_cut(g.get('rationale'), 400)}" if g.get("rationale") else "",
            f"  - caveat: {_cut(g.get('caveat'), 300)}" if g.get("caveat") else "",
            f"  - experiment: {_cut(g.get('experiment'), 300)}" if g.get("experiment") else "",
            f"  - reason: {_cut(g.get('reason'), 200)}" if g.get("reason") else "",
            *(f"  - evidence: {_cut(q, 260)}" for q in (g.get("intervention_evidence") or [])[:1]),
            *(f"  - mechanism evidence: {_cut(q, 260)}" for q in (g.get("mechanism_evidence") or [])[:1]),
        ] if x),
        f"- {g['intervention_label']} → {g['mechanism_label']} ({g['status']})") for g in gaps],
        share=.05, intro="A mechanism established in the disease and an intervention acting on it that "
                         "the literature has not (or has) tested in the disease.")

    def candidate(i: int, c: dict) -> tuple[str, str]:
        flags = [x for x in ["tested without benefit in the disease" if c.get("tested_without_benefit_in_input") else "",
                             "generic technique" if c.get("generic") else "",
                             "endpoint of another disease" if c.get("other_disease_endpoint") else ""] if x]
        info = (f"{c['kind'].replace('_', ' ')}, {c['category']}, score {c['score']}"
                + (f", {c['n_papers']} papers" if c.get("n_papers") is not None else f", {len(c['papers'])} papers")
                + (f", best evidence {c['best_level']}" if c.get("best_level") else "")
                + (f", tier {c['tier']}/3" if c.get("tier") else "")
                + (f" — {'; '.join(flags)}" if flags else ""))
        n = nodes.get(c["id"]) or {}
        extra = _entity_facts(n)
        lines = [f"{i}. **{c['label']}** ({info})" + (f" — {extra}" if extra else "")]
        for p in c["paths"][:3]:
            neg = f", {p['negative_papers']} negative" if p.get("negative_papers") else ""
            lines.append(f"   - {p['edge']} ({p['level']}, {p['papers']} papers{neg}, confidence "
                         f"{p['confidence']}); link to the disease: {p['bridge']}")
            lines += [f"     - {_cut(q, 240)}" for q in p.get("evidence", [])[:1]]
        return "\n".join(lines), f"{i}. {c['label']} ({info})"
    cands = sorted(view["candidates"], key=lambda c: -c["score"])
    for cat, title, share in (("direct", "Candidates already applied to the disease", .2),
                              ("transfer", "Transfer candidates (from related diseases or mechanisms)", .12)):
        cs = [c for c in cands if c["category"] == cat]
        w.section(f"{title} ({len(cs)})", [candidate(i, c) for i, c in enumerate(cs, 1)], share=share)
    rest = [c for c in cands if c["category"] not in ("direct", "transfer")]
    w.section(f"Other candidates ({len(rest)})", [candidate(i, c) for i, c in enumerate(rest, 1)], share=.01)

    def weight(e):
        return LEVEL_RANK.get(e["level"], 0) * e["confidence"] * math.log2(2 + e["papers"])
    edges = sorted(view["edges"], key=weight, reverse=True)
    rows = []
    for e in edges:
        neg = f", {e['negative_papers']} negative" + (" (mostly negative)" if e.get("mostly_negative") else "") \
            if e.get("negative_papers") else ""
        head = (f"- {label(e['from'])} —{e['relation'].replace('_', ' ')}→ {label(e['to'])} "
                f"({e['level']}, {e['papers']} papers{neg}, confidence {e['confidence']})")
        ev = e["evidence"][0] if e["evidence"] else None
        short = f"- {label(e['from'])} —{e['relation'].replace('_', ' ')}→ {label(e['to'])} ({e['level']}, {e['papers']})"
        rows.append((head + (f": \"{_cut(ev['quote'], 220)}\" ({ev['paper']})" if ev else ""), short))
    w.section(f"Evidence edges ({len(edges)}, strongest first; short rows: level, papers)", rows, share=.32)

    def paper(p):
        title = _cut(p.get("title"), 200)
        meta = ", ".join(str(x) for x in (p.get("journal"), p.get("year"),
                                          f"cited by {p['cited_by']}" if p.get("cited_by") else None,
                                          (p.get("connection") or "").replace("_", " ") or None,
                                          f"relevance {p['relevance']}/3" if p.get("relevance") else None) if x)
        head = f"- **{p['key']}** {title} ({meta})"
        return head + (f"\n  {_cut(p.get('summary'), 450)}" if p.get("summary") else ""), head
    ps = sorted(papers.values(), key=lambda p: (-(p.get("relevance") or 0), -(p.get("cited_by") or 0)))
    w.section(f"Papers read ({len(ps)})", [paper(p) for p in ps], share=.17)

    # every entity, grouped by kind, most-cited first
    by_kind = defaultdict(list)
    for n in sorted(view["nodes"], key=lambda n: -len(n["papers"])):
        by_kind[n["kind"]].append(n)
    rows = []
    for kind, ns in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        full = ", ".join(f"{n['label']} ({len(n['papers'])})" for n in ns)
        rows.append((f"- **{kind.replace('_', ' ')}** ({len(ns)}): {full}",
                     f"- **{kind.replace('_', ' ')}** ({len(ns)}): {_cut(full, 600)}"))
    w.section("All entities by kind (number of papers)", rows, share=.13)
    return w.text()


def _entity_facts(n: dict) -> str:
    g, d = n.get("gene") or {}, n.get("drug") or {}
    bits = []
    if g:
        bits += [x for x in (g.get("name"), g.get("locus") and f"locus {g['locus']}",
                             g.get("function") and _cut(g["function"], 200)) if x]
    if d:
        bits += [x for x in (d.get("type"), d.get("stage") and f"stage {d['stage']}",
                             d.get("mechanisms") and "mechanism: " + ", ".join(d["mechanisms"][:3]),
                             d.get("warnings") and "warnings: " + ", ".join(d["warnings"][:3])) if x]
    ph = n.get("phenotype") or {}
    if ph.get("lay") or ph.get("definition"):
        bits.append(_cut(ph.get("lay") or ph.get("definition"), 160))
    return "; ".join(bits)


# -- overview ------------------------------------------------------------------------------
def present_md(view: dict, budget: int = BUDGET) -> str:
    f, items = view["focus"], view["items"]
    w = Writer(budget)
    w.add([f"# {f['label']}", ""] + ([_cut(f["description"], 2000), ""] if f.get("description") else [])
          + [f"- **{x['label']}:** {x['value']}" for x in f.get("facts", [])]
          + ([f"- **Also known as:** {'; '.join(f['synonyms'])}"] if f.get("synonyms") else [])
          + ["", f"{len(items)} entries in {len(view['groups'])} topics, {len(view['links'])} connections "
                 "between entries. Entries are listed by topic, most relevant first.", ""])
    conn = defaultdict(list)
    for x in view["links"]:
        conn[x["a"]].append((x["label"], x["b"]))
        conn[x["b"]].append((x["label"], x["a"]))

    def entry(it: dict) -> tuple[str, str]:
        g, d, t = it.get("gene") or {}, it.get("drug") or {}, it.get("trial") or {}
        bits = [x for x in (
            it.get("frequency"), it.get("status"), it.get("phase") and f"phase {it['phase']}",
            it.get("country"), it.get("sponsors") and "sponsors: " + ", ".join(it["sponsors"][:3]),
            t.get("start") and f"{t['start']}–{t.get('end') or ''}", t.get("why_stopped") and f"stopped: {t['why_stopped']}",
            it.get("lay") or it.get("definition"), it.get("hallmark"),
            it.get("description") and _cut(it["description"], 300),
            g.get("locus") and f"locus {g['locus']}", g.get("function") and _cut(g["function"], 250),
            g.get("tractable") and "tractable: " + ", ".join(g["tractable"]),
            d.get("type"), d.get("stage") and f"stage {d['stage']}",
            d.get("mechanisms") and "mechanism: " + ", ".join(d["mechanisms"][:3]),
            d.get("warnings") and "warnings: " + ", ".join(d["warnings"][:4]),
            it.get("variants") and "variants: " + ", ".join(f"{k} {v}" for k, v in it["variants"].items()),
            *it.get("notes", []),
            it.get("sources") and "sources: " + ", ".join(it["sources"]),
        ) if x]
        links = defaultdict(list)
        for lab, other in conn.get(it["id"], ()):
            if other in items:
                links[lab].append(items[other]["label"])
        rel = "; ".join(f"{lab}: {', '.join(sorted(set(xs)))}" for lab, xs in links.items())
        full = f"- **{it['label']}** ({it['kind'].replace('_', ' ')})" + (f" — {'; '.join(bits)}" if bits else "") \
            + (f"\n  - connections: {rel}" if rel else "")
        short = f"- {it['label']}" + (f" — {_cut('; '.join(bits), 160)}" if bits else "")
        return full, short

    sections = view["sections"]
    for sec in sections:
        rows = []
        for g in (g for g in view["groups"] if g["section"] == sec["id"]):
            rows.append((f"### {g['label']} ({g['count']})", f"### {g['label']} ({g['count']})"))
            rows += [entry(items[i]) for i in g["items"] if i in items]
        w.section(sec["label"], rows, share=1 / max(1, len(sections)))
    w.add([f"Note: {n}" for n in view.get("notes", [])])
    return w.text()


def write(view_path: Path, out: Path | None = None, budget: int = BUDGET) -> Path:
    view = json.loads(view_path.read_text(encoding="utf-8"))
    out = out or view_path.with_suffix(".md")
    out.write_text((evidence_md if "candidates" in view else present_md)(view, budget), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("views", nargs="+", type=Path, help="exported .present.json / .evidence.json")
    ap.add_argument("-o", "--out", type=Path, help="output .md (single input; default: next to it)")
    ap.add_argument("--budget", type=int, default=BUDGET, help=f"characters (default {BUDGET})")
    args = ap.parse_args()
    if args.out and len(args.views) > 1:
        ap.error("-o needs a single input")
    for v in args.views:
        out = write(v, args.out, args.budget)
        print(f"{v.name} -> {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
