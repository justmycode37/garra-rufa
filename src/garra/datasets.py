"""Startup datasets that may be missing from a checkout, and where to get them back.

`ensure(path)` returns `path` when the file exists. When it is missing, it is restored
from the tracked deployment bundle (deploy/discovery/*.gz, checksummed in its manifest)
or downloaded from the official release URL, in that order. When neither works it
returns None and the caller degrades: the server still starts and those routes fall
back to live APIs or answer 503.
"""
import gzip
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

from garra.paths import DATA_DIR, ROOT

BUNDLE = ROOT / "deploy" / "discovery"
# Official release files, by file name. Only names listed here are ever downloaded.
RELEASES = {
    "hp.obo": "https://purl.obolibrary.org/obo/hp.obo",
    "phenotype.hpoa": "https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa",
    "genes_to_disease.txt": "https://purl.obolibrary.org/obo/hp/hpoa/genes_to_disease.txt",
}
# Where the server and dev-all look for each startup file by default.
DEFAULTS = {
    "bridge": DATA_DIR / "derived" / "discovery" / "bridge.json",
    "ontology": DATA_DIR / "ontology" / "hp.obo",
    "atlas": DATA_DIR / "atlas.sqlite",
    "region_map": ROOT / "webapp" / "src" / "lib" / "body-regions.json",
}
_tried = {}


def _log(message):
    print(f"[datasets] {message}", file=sys.stderr, flush=True)


def _from_bundle(name, target):
    manifest = BUNDLE / "manifest.json"
    if not (BUNDLE / (name + ".gz")).exists() or not manifest.exists():
        return False
    record = json.loads(manifest.read_text()).get("files", {}).get(name)
    if not record:
        return False
    with gzip.open(BUNDLE / (name + ".gz"), "rb") as stream:
        raw = stream.read(record["bytes"] + 1)
    if len(raw) != record["bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
        _log(f"{name}: bundle checksum mismatch, not used")
        return False
    target.write_bytes(raw)
    return True


def _from_release(name, target):
    url = RELEASES.get(name)
    if not url:
        return False
    request = urllib.request.Request(url, headers={"User-Agent": "garra-rufa/0.1"})
    part = target.with_name(target.name + ".part")
    try:
        with urllib.request.urlopen(request, timeout=120) as response, open(part, "wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
    return True


def ensure(path, *, download=True):
    """`path` if it exists or could be restored, else None (logged once per path)."""
    if path is None:
        return None
    path = Path(path)
    if path.exists():
        return path
    if path in _tried:
        return _tried[path]
    result = None
    for label, restore in (("deploy bundle", _from_bundle),
                           ("official release", _from_release if download else None)):
        if restore is None:
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if restore(path.name, path):
                _log(f"{path.name}: missing, restored from the {label}")
                result = path
                break
        except Exception as exc:  # offline, moved release, unwritable directory
            _log(f"{path.name}: {label} unavailable ({exc!r})")
    if result is None:
        _log(f"{path} is missing; continuing without it")
    _tried[path] = result
    return result
