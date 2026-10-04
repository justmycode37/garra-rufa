"""Export the local discovery snapshot for reproducible hosted builds."""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "bridge.json": ROOT / "data/derived/discovery/bridge.json",
    "hp.obo": ROOT / "data/ontology/hp.obo",
    "atlas.sqlite": ROOT / "data/atlas.sqlite",
    "body-regions.json": ROOT / "webapp/src/lib/body-regions.json",
}


def main():
    destination = ROOT / "deploy/discovery"
    # Read everything before replacing the previous export.
    contents = {name: path.read_bytes() for name, path in SOURCES.items()}
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "files": {}}
    for name, raw in contents.items():
        packed = gzip.compress(raw, mtime=0)
        (destination / (name + ".gz")).write_bytes(packed)
        manifest["files"][name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Exported discovery bundle (bridge, ontology, names and regions)")


if __name__ == "__main__":
    main()
