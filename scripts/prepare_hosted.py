"""Fetch only the two public HPO files needed by the hosted anatomical index."""
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from garra.discovery.hosted import prepare_bundle  # noqa: E402

prepare_bundle(ROOT / "deploy/discovery", ROOT / "data/hosted")
root = ROOT / "data" / "hpo"
root.mkdir(parents=True, exist_ok=True)
for name, url in {
    "hp.obo": "https://purl.obolibrary.org/obo/hp.obo",
    "phenotype.hpoa": "https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa",
}.items():
    destination = root / name
    if destination.exists() and destination.stat().st_size > 1000:
        continue
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        total = 0
        with urlopen(url, timeout=60) as response, temporary.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise RuntimeError("HPO download exceeded its size limit")
                target.write(chunk)
        prefix = temporary.read_bytes()[:5000]
        if total < 1000 or (name.endswith("obo") and b"format-version:" not in prefix) or (name.endswith("hpoa") and b"database_id" not in prefix):
            raise RuntimeError("HPO download did not contain the expected dataset")
        temporary.replace(destination)
        print("Prepared " + name)
    finally:
        temporary.unlink(missing_ok=True)
