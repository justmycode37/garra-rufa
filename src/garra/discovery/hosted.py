"""Verify a versioned deployment bundle and load the same engines used locally."""
import gzip
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from garra.discovery.engine import DiscoveryEngine
from garra.discovery.regions import RegionIndex

FILES = {"bridge.json", "hp.obo", "atlas.sqlite", "body-regions.json"}


def prepare_bundle(source, destination):
    source, destination = Path(source), Path(destination)
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("schema_version") != 1 or set(manifest.get("files", {})) != FILES:
        raise ValueError("Invalid discovery bundle manifest")
    destination.mkdir(parents=True, exist_ok=True)
    for name in sorted(FILES):
        record = manifest["files"][name]
        with gzip.open(source / (name + ".gz"), "rb") as stream:
            raw = stream.read(100 * 1024 * 1024 + 1)
        if len(raw) != record["bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("Discovery bundle checksum mismatch: " + name)
        (destination / name).write_bytes(raw)
    return manifest


@lru_cache(maxsize=1)
def load_discovery(root):
    directory = Path(root) / "data/hosted"
    bridge = json.loads((directory / "bridge.json").read_text())
    engine = DiscoveryEngine(bridge, atlas=directory / "atlas.sqlite")
    regions = RegionIndex(engine, bridge, json.loads((directory / "body-regions.json").read_text()), directory / "hp.obo")
    return engine, regions
