#!/usr/bin/env python3
"""Export the patient overview (present.py) and the evidence graph (evidence/main.py) of
runs as static JSON for the webapp's graph pages (webapp/src/app/graphs).

Each input is a graph JSON of main.py; the evidence graph is read from the
<stem>.kg.json next to it when it exists (or pass a .kg.json alone for the evidence graph
only). Per run, <out>/<stem>.present.json and <out>/<stem>.evidence.json are written and
<out>/index.json (the list the webapp offers) is updated: entries of other runs are kept.

Usage:
  python src/query-test/web_export.py runs/marfan.json runs/pmm2.json runs/pompe.json
  python src/query-test/web_export.py runs/marfan.json --focus MONDO:0007947
  python src/query-test/web_export.py runs/pmm2.smoke.kg.json -o webapp/public/graph-data
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from present import Graph, Present  # noqa: E402

OUT = HERE.parents[1] / "webapp" / "public" / "graph-data"


def _dump(view: dict, path: Path) -> int:
    text = json.dumps(view, ensure_ascii=False, separators=(",", ":"), default=str)
    path.write_text(text, encoding="utf-8")
    return len(text)


def export_present(graph_path: Path, focus: str | None, similar: int) -> dict:
    g = Graph(json.loads(graph_path.read_text(encoding="utf-8")))
    fid = g.pick_focus(focus)
    if not fid:
        raise SystemExit(f"{graph_path}: no disease to present"
                         + (f" (focus {focus!r} not found)" if focus else ""))
    return Present(g, fid, similar=similar).build()


def export_evidence(kg_path: Path) -> dict:
    from evidence.main import viewer_data  # heavy imports: only when a .kg.json is exported
    return viewer_data(json.loads(kg_path.read_text(encoding="utf-8")))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path,
                    help="graph JSON of main.py (its <stem>.kg.json is picked up) or a .kg.json")
    ap.add_argument("-o", "--out", type=Path, default=OUT, help=f"output directory (default {OUT})")
    ap.add_argument("--focus", help="disease to present (single input only; see present.py)")
    ap.add_argument("--similar", type=int, default=8, help="see present.py")
    args = ap.parse_args()
    if args.focus and len(args.inputs) > 1:
        ap.error("--focus needs a single input")
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    args.out.mkdir(parents=True, exist_ok=True)
    index_path = args.out / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    entries = {e["id"]: e for e in index}

    for path in args.inputs:
        if path.name.endswith(".kg.json"):
            stem, graph_path, kg_path = path.name.removesuffix(".kg.json"), None, path
        else:
            stem, graph_path = path.name.removesuffix(".json"), path
            kg_path = path.with_name(stem + ".kg.json")
            kg_path = kg_path if kg_path.exists() else None
        entry = {"id": stem, "label": stem, "present": None, "evidence": None,
                 "exported": date.today().isoformat()}
        if graph_path:
            view = export_present(graph_path, args.focus, args.similar)
            entry["present"] = f"{stem}.present.json"
            entry["label"] = view["focus"]["label"]
            entry["presentStats"] = {"items": len(view["items"]), "groups": len(view["groups"]),
                                     "links": len(view["links"])}
            size = _dump(view, args.out / entry["present"])
            print(f"{stem}: overview of {view['focus']['label']} — {len(view['items'])} items, "
                  f"{len(view['links'])} connections ({size // 1024} KB)")
        if kg_path:
            view = export_evidence(kg_path)
            entry["evidence"] = f"{stem}.evidence.json"
            if not graph_path:
                entry["label"] = view["disease"] or stem
            entry["evidenceStats"] = {"nodes": len(view["nodes"]), "edges": len(view["edges"]),
                                      "candidates": len(view["candidates"]),
                                      "papers": len(view["papers"])}
            size = _dump(view, args.out / entry["evidence"])
            print(f"{stem}: evidence graph — {len(view['nodes'])} nodes, {len(view['edges'])} "
                  f"edges, {len(view['candidates'])} candidates ({size // 1024} KB)")
        elif graph_path:
            print(f"{stem}: no {stem}.kg.json next to it, overview only")
        entries[stem] = entry

    index = sorted(entries.values(), key=lambda e: e["label"].lower())
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {args.out.resolve()} ({len(index)} runs in index.json)")


if __name__ == "__main__":
    main()
