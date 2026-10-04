"""python -m garra.local download|build|status [--only GROUP ...]"""

from __future__ import annotations

import argparse
import importlib
import sys
import time

from garra.local import LOCAL_DIR, RAW, db_path
from garra.local import download as dl

# index name -> builder module (garra.local.build_<module>); order = build order
BUILDS = {
    "ontology": "ontology",
    "mesh": "mesh",
    "orphadata": "orphadata",
    "hpo": "hpo",
    "monarch": "monarch",
    "opentargets": "opentargets",
    "clinvar": "clinvar",
    "gtex": "gtex",
    "hpa": "hpa",
    "clinicaltrials": "clinicaltrials",
    "reporter": "reporter",
    "pubmed": "pubmed",  # before pubtator: annotations are kept for the PubMed subset
    "pubtator": "pubtator",
}


def _size(path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.is_dir() else 0


def status() -> int:
    total = 0
    print(f"{'index':16} {'sqlite':>10} {'raw':>10}")
    for name in BUILDS:
        s, r = _size(db_path(name)), _size(RAW / name)
        total += s + r
        print(f"{name:16} {s / 1e9:9.2f}G {r / 1e9:9.2f}G")
    print(f"{'total':16} {total / 1e9:20.2f}G  ({LOCAL_DIR})")
    return 0


def build(only: list[str] | None, keep_raw: bool) -> int:
    failed = 0
    for name, mod in BUILDS.items():
        if only and name not in only:
            continue
        m = importlib.import_module(f"garra.local.build_{mod}")
        if not m.ready():
            print(f"skip {name}: raw files missing (download first)", file=sys.stderr)
            continue
        t0 = time.time()
        print(f"build {name} ...", file=sys.stderr, flush=True)
        try:
            m.build(keep_raw=keep_raw)
        except Exception as e:  # keep building the others
            failed += 1
            print(f"FAILED {name}: {e!r}", file=sys.stderr)
            continue
        print(f"built {name}: {_size(db_path(name)) / 1e9:.2f} GB in "
              f"{(time.time() - t0) / 60:.1f} min", file=sys.stderr, flush=True)
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m garra.local", description=__doc__)
    ap.add_argument("command", choices=["download", "build", "status"])
    ap.add_argument("--only", action="append", help="group / index name (repeatable)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--keep-raw", action="store_true",
                    help="keep large intermediate downloads that a build has consumed")
    a = ap.parse_args(argv)
    if a.command == "download":
        return dl.download(a.only, a.workers)
    if a.command == "build":
        return build(a.only, a.keep_raw)
    return status()
