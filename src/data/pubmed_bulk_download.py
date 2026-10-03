#!/usr/bin/env python3
"""Bulk download PubMed/MEDLINE records (https://pubmed.ncbi.nlm.nih.gov/download/).

Source: NLM FTP-over-HTTPS, https://ftp.ncbi.nlm.nih.gov/pubmed/
  baseline/     annual snapshot, pubmed<yy>n<NNNN>.xml.gz  (~1300 files, tens of GB)
  updatefiles/  daily additions/changes/deletions since the baseline

Every file has a .md5 sidecar; it is checked after download. Downloads are
resumable (.part files + Range) and verified files are skipped, so re-running
continues where it left off. Progress shows per-file and overall bytes, speed, ETA.

Usage:
  python src/data/pubmed_bulk_download.py --list                 # show sizes, download nothing
  python src/data/pubmed_bulk_download.py --limit 3              # try the first 3 baseline files
  python src/data/pubmed_bulk_download.py                        # full baseline -> <repo>/data/pubmed
  python src/data/pubmed_bulk_download.py -p baseline updatefiles
  python src/data/pubmed_bulk_download.py --workers 3 -o D:/pubmed
"""
import argparse
import hashlib
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "https://ftp.ncbi.nlm.nih.gov/pubmed"
CHUNK = 1 << 20
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "data" / "pubmed-nih-gov"
UA = {"User-Agent": "pubmed-bulk-download/1.0 (research use)"}
FILE_RE = re.compile(r'href="(pubmed\d+n\d+\.xml\.gz)"')


def fetch(url, start=0, method="GET"):
    headers = dict(UA)
    if start:
        headers["Range"] = f"bytes={start}-"
    req = urllib.request.Request(url, headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=60)


LISTING_RE = re.compile(r'href="(pubmed\d+n\d+\.xml\.gz)"[^\n]*?\s(\d+(?:\.\d+)?)([KMG]?)\s*$', re.M)
UNITS = {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30}


def list_files(part):
    """[(name, approx_size)] from the directory listing (exact size is read at download time)."""
    with fetch_retry(f"{BASE}/{part}/") as r:
        html = r.read().decode()
    return sorted((n, int(float(v) * UNITS[u])) for n, v, u in LISTING_RE.findall(html))


def fetch_retry(url, start=0, tries=6):
    """fetch() with backoff on 429/5xx (NCBI rate-limits bursts)."""
    for i in range(tries):
        try:
            return fetch(url, start)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or i == tries - 1:
                raise
            time.sleep(2 ** i)


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def expected_md5(url):
    with fetch_retry(url + ".md5") as r:
        m = re.search(r"[0-9a-f]{32}", r.read().decode())
    if not m:
        raise IOError(f"no md5 in {url}.md5")
    return m.group(0)


class Progress:
    def __init__(self, total_files, total_bytes):
        self.total_files, self.total_bytes = total_files, total_bytes
        self.files_done = self.bytes_done = 0
        self.active = {}
        self.t0 = time.time()
        self.session_bytes = 0
        self.lock = threading.Lock()
        self.last = 0

    def resize(self, delta):
        with self.lock:
            self.total_bytes += delta

    def add(self, name, n, already=0):
        with self.lock:
            self.bytes_done += n + already
            self.session_bytes += n
            self.active[name] = self.active.get(name, 0) + n
            self.render()

    def finish(self, name, skipped_bytes=0):
        with self.lock:
            self.active.pop(name, None)
            self.files_done += 1
            self.bytes_done += skipped_bytes
            self.render(force=True)

    def log(self, msg):
        with self.lock:
            sys.stdout.write("\r" + " " * 100 + "\r" + msg + "\n")
            self.render(force=True)

    def render(self, force=False):
        now = time.time()
        if not force and now - self.last < 0.2:
            return
        self.last = now
        pct = min(100, 100 * self.bytes_done / self.total_bytes) if self.total_bytes else 100
        speed = self.session_bytes / max(now - self.t0, 1e-6)
        left = max(0, self.total_bytes - self.bytes_done)
        eta = time.strftime("%H:%M:%S", time.gmtime(left / speed)) if speed > 0 and self.session_bytes else "--:--:--"
        w = 30
        bar = "#" * int(w * pct / 100) + "-" * (w - int(w * pct / 100))
        sys.stdout.write(
            f"\r[{bar}] {pct:5.1f}%  {self.files_done}/{self.total_files} files  "
            f"{self.bytes_done / 1e9:6.2f}/{self.total_bytes / 1e9:.2f} GB  "
            f"{speed / 1e6:5.1f} MB/s  ETA {eta}  "
        )
        sys.stdout.flush()


def download(part, name, approx, out_dir, prog):
    url = f"{BASE}/{part}/{name}"
    dest, partial, marker = out_dir / name, out_dir / (name + ".part"), out_dir / (name + ".ok")

    if dest.exists():
        if not marker.exists():
            if md5_of(dest) != expected_md5(url):
                dest.unlink()
            else:
                marker.touch()
        if dest.exists():
            prog.resize(dest.stat().st_size - approx)
            prog.finish(name, skipped_bytes=dest.stat().st_size)
            return

    have = partial.stat().st_size if partial.exists() else 0
    resp = fetch_retry(url, start=have)
    if have and resp.status != 206:  # Range ignored; restart
        have = 0
    size = have + int(resp.headers["Content-Length"])
    prog.resize(size - approx)
    if have:
        prog.add(name, 0, already=have)
    with resp, open(partial, "ab" if have else "wb") as f:
        while chunk := resp.read(CHUNK):
            f.write(chunk)
            prog.add(name, len(chunk))

    if partial.stat().st_size != size:
        raise IOError(f"{name}: got {partial.stat().st_size} bytes, expected {size}")
    if md5_of(partial) != expected_md5(url):
        partial.unlink()
        raise IOError(f"{name}: md5 mismatch")
    partial.replace(dest)
    marker.touch()
    prog.finish(name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--parts", nargs="+", choices=["baseline", "updatefiles"], default=["baseline"])
    ap.add_argument("-o", "--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, help="only the first N files of each part")
    ap.add_argument("--workers", type=int, default=2, help="parallel downloads (NCBI allows a few)")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--list", action="store_true", help="list files and total size, then exit")
    args = ap.parse_args()

    jobs = []
    for part in args.parts:
        print(f"Listing {part} ...", flush=True)
        files = list_files(part)
        if args.limit:
            files = files[: args.limit]
        jobs += [(part, n, s) for n, s in files]
    total = sum(s for _, _, s in jobs)
    print(f"{len(jobs)} files, ~{total / 1e9:.2f} GB -> {args.out}")
    if args.list:
        return

    prog = Progress(len(jobs), total)

    def run(job):
        part, name, size = job
        out_dir = Path(args.out) / part
        out_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, args.retries + 1):
            try:
                return download(part, name, size, out_dir, prog)
            except (urllib.error.URLError, IOError, TimeoutError) as e:
                prog.log(f"error {name}: {e} (attempt {attempt}/{args.retries})")
                time.sleep(5 * attempt)
        raise IOError(name)

    failures = []
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(run, j): j for j in jobs}
        try:
            for f in as_completed(futs):
                if f.exception():
                    failures.append(futs[f][1])
        except KeyboardInterrupt:
            for f in futs:
                f.cancel()
            print("\nInterrupted; re-run to resume.")
            sys.exit(130)

    print()
    if failures:
        print("Failed (re-run to resume):", *failures, sep="\n  ")
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()
