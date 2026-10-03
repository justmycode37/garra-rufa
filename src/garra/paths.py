"""Project paths shared by ingest, graph, and CLI."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
ATLAS_DB = DATA_DIR / "atlas.sqlite"


def raw_file(source_id: str, filename: str) -> Path:
    return RAW_DIR / source_id / filename
