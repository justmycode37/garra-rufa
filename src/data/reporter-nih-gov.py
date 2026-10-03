#!/usr/bin/env python3
"""Bulk download NIH RePORTER ExPORTER data (https://reporter.nih.gov/exporter).

Dataset types (URL pattern https://reporter.nih.gov/exporter/<kind>/download[/<FY>]):
  projects   per-FY zip  RePORTER_PRJ_C_FY<yr>.zip      (FY1985 - present)
  abstracts  per-FY zip  RePORTER_PRJABS_C_FY<yr>.zip
  pubs       per-FY zip  RePORTER_PUB_C_FY<yr>.zip      (publications)
  linktables per-FY zip  RePORTER_PUBLNK_C_FY<yr>.zip   (publication <-> project links)
  patents    single CSV  Patents.csv
  clinical   single CSV  ClinicalStudies.csv

Each URL 302-redirects to a short-lived token URL on public.era.nih.gov, so the
redirect is resolved fresh for every file. Downloads are resumable and
already-complete files are skipped.

Usage:
  python src/data/reporter-nih-gov.py             # everything -> <repo>/data
  python src/data/reporter-nih-gov.py -k projects abstracts --start 2015 --end 2024
  python src/data/reporter-nih-gov.py -o D:/nih --delay 2
"""
import argparse
import datetime
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://reporter.nih.gov/exporter"
FIRST_FY = 1985
CHUNK = 1 << 20

# Default output: <repo root>/data (this file lives in <repo root>/src/data)
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "data" / "reporter-nih-gov"

# kind -> (url path segment, per-fiscal-year?)
KINDS = {
    "projects": ("projects", True),
    "abstracts": ("abstracts", True),
    "pubs": ("publications", True),
    "linktables": ("linktables", True),
    "patents": ("patents", False),
    "clinical": ("clinicalstudies", False),
}

UA = {"User-Agent": "reporter-bulk-download/1.0 (research use)"}


def open_url(url, start=0):
    headers = dict(UA)
    if start:
        headers["Range"] = f"bytes={start}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120)


def filename_from(resp, fallback):
    cd = resp.headers.get("Content-Disposition", "")
    if "filename=" in cd:
        return cd.split("filename=", 1)[1].strip().strip('"; ')
    return fallback


def bar(done, total, label, t0, idx=0, n=1, width=30):
    """One-line progress. Server sends no file size, so the bar tracks files overall
    (idx of n) and the MB/speed counter tracks the current file."""
    speed = done / max(time.time() - t0, 1e-6) / 1e6
    fill = int(width * (idx - 1) / n) if n else 0
    if total:  # size known: bar is this file's progress
        fill = int(width * min(done / total, 1.0))
    return (f"\r  [{'#' * fill}{'-' * (width - fill)}] {idx}/{n} {label[:34]:34} "
            f"{done / 1e6:8.1f} MB {speed:5.1f} MB/s")


def download(url, out_dir, fallback_name, idx=0, n=1):
    """Download one file. Returns the Path, or None if the server has no such file."""
    # Probe for the real filename first (a fresh token URL each time).
    try:
        probe = open_url(url)
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return None
        raise
    name = filename_from(probe, fallback_name)
    total = int(probe.headers.get("Content-Length") or 0)
    dest, part = out_dir / name, out_dir / (name + ".part")

    if dest.exists() and (not total or dest.stat().st_size == total):
        probe.close()
        print(f"  skip   {name} (already downloaded)")
        return dest

    have = part.stat().st_size if part.exists() else 0
    if have and have < total:
        probe.close()
        resp = open_url(url, start=have)  # new token, ranged
        if resp.status != 206:  # server ignored Range; restart
            have = 0
    else:
        have, resp = 0, probe
    mode = "ab" if have else "wb"

    with resp, open(part, mode) as f:
        done, t0 = have, time.time()
        label = name
        while chunk := resp.read(CHUNK):
            f.write(chunk)
            done += len(chunk)
            print(bar(done - have, total - have if total else 0, label, t0, idx, n), end="", flush=True)
    print()

    if total and part.stat().st_size != total:
        raise IOError(f"{name}: got {part.stat().st_size} bytes, expected {total}")
    if name.endswith(".zip") and not zipfile.is_zipfile(part):
        raise IOError(f"{name}: not a valid zip")
    part.replace(dest)
    return dest


def main():
    cur = datetime.date.today().year
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-k", "--kinds", nargs="+", choices=list(KINDS), default=list(KINDS))
    ap.add_argument("-o", "--out", default=str(DEFAULT_OUT))
    ap.add_argument("--start", type=int, default=FIRST_FY, help="first fiscal year")
    ap.add_argument("--end", type=int, default=cur, help="last fiscal year")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests (NIH asks <=1 req/s)")
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    root = Path(args.out)
    failures = []
    for kind in args.kinds:
        seg, per_year = KINDS[kind]
        out_dir = root / kind
        out_dir.mkdir(parents=True, exist_ok=True)
        targets = (
            [(f"{BASE}/{seg}/download/{y}", f"{kind}_{y}.zip") for y in range(args.start, args.end + 1)]
            if per_year
            else [(f"{BASE}/{seg}/download", f"{kind}.csv")]
        )
        print(f"[{kind}] -> {out_dir}")
        for i, (url, fallback) in enumerate(targets, 1):
            for attempt in range(1, args.retries + 1):
                try:
                    if download(url, out_dir, fallback, i, len(targets)) is None:
                        print(f"  none   {url} (not available)")
                    break
                except (urllib.error.URLError, IOError, TimeoutError) as e:
                    print(f"\n  error  {url}: {e} (attempt {attempt}/{args.retries})")
                    time.sleep(5 * attempt)
            else:
                failures.append(url)
            time.sleep(args.delay)

    if failures:
        print("\nFailed (re-run to resume):", *failures, sep="\n  ")
        sys.exit(1)
    print("\nDone.")


if __name__ == "__main__":
    main()
