"""python -m garra.local download|build|status|prune|bench [--only GROUP ...]

  download   fetch the bulk files into data/local/raw/ (resumes, skips finished files)
  build      build data/local/<index>.sqlite from them (each index replaces its old copy)
  status     sizes of the indexes and raw files
  prune      delete large raw inputs that a built index has fully consumed; `download`
             fetches them again if an index has to be rebuilt
  bench      replay a GARRA_LOCAL_PROFILE log against the local handlers (see bench.py)
"""

from __future__ import annotations

import argparse
import importlib
import shutil
import sys
import time
from pathlib import Path

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


# index -> raw paths (relative to data/local/raw) it no longer needs once built
CONSUMED = {
    "opentargets": ["opentargets/evidence_europepmc", "opentargets/baseline_expression"],
    "gtex": ["gtex/GTEx_Analysis_v10_eQTL.tar", "gtex/GTEx_Analysis_v10_sQTL.tar",
             "gtex/GTEx_Analysis_2021-02-11_v10_WholeGenomeSeq_953Indiv.lookup_table.txt.gz"],
    "clinicaltrials": ["clinicaltrials/ctg-studies.json.zip"],
    "pubtator": ["pubtator"],
}


def prune(only: list[str] | None = None) -> int:
    freed = 0
    for name, paths in CONSUMED.items():
        if (only and name not in only) or not db_path(name).is_file():
            continue
        for rel in paths:
            p = RAW / rel
            n = _size(p)
            if not n:
                continue
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            freed += n
            print(f"removed {rel} ({n / 1e9:.2f} GB)")
    print(f"freed {freed / 1e9:.2f} GB")
    return 0


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
    extra = sum(p.stat().st_size for p in LOCAL_DIR.glob("*.bin"))
    total += extra
    print(f"{'other (*.bin)':16} {extra / 1e9:9.2f}G")
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
            m.build()
        except Exception as e:  # keep building the others
            failed += 1
            print(f"FAILED {name}: {e!r}", file=sys.stderr)
            continue
        print(f"built {name}: {_size(db_path(name)) / 1e9:.2f} GB in "
              f"{(time.time() - t0) / 60:.1f} min", file=sys.stderr, flush=True)
        if not keep_raw and name in CONSUMED:
            prune([name])
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m garra.local", description=__doc__)
    ap.add_argument("command", choices=["download", "build", "status", "prune", "bench"])
    ap.add_argument("log", nargs="?", type=Path, help="bench: GARRA_LOCAL_PROFILE log")
    ap.add_argument("--repeat", type=int, default=2, help="bench: passes over the log")
    ap.add_argument("--only", action="append", help="group / index name (repeatable)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--keep-raw", action="store_true",
                    help="keep the large raw inputs a build consumes (see prune)")
    a = ap.parse_args(argv)
    if a.command == "download":
        return dl.download(a.only, a.workers)
    if a.command == "build":
        return build(a.only, a.keep_raw)
    if a.command == "prune":
        return prune(a.only)
    if a.command == "bench":
        if not a.log:
            ap.error("bench needs a profile log")
        from garra.local import bench
        return bench.run(a.log, a.repeat)
    return status()
