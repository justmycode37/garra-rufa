"""Build data/atlas.sqlite from whatever raw files exist."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from garra.paths import ATLAS_DB, RAW_DIR
from garra.sources.catalog import BULK_SOURCES

from . import loaders


def expected_bulk_paths() -> dict[str, Path]:
    return {
        source_id: RAW_DIR / source_id / source.filename
        for source_id, source in BULK_SOURCES.items()
        if source.default
    }


def missing_sources() -> list[dict]:
    missing = []
    for source_id, path in expected_bulk_paths().items():
        if not path.is_file():
            source = BULK_SOURCES[source_id]
            missing.append(
                {
                    "source_id": source_id,
                    "expected_path": str(path),
                    "filename": source.filename,
                }
            )
    return missing


def build_atlas(db_path: Path | None = None) -> dict:
    db_path = db_path or ATLAS_DB
    conn = loaders.init_db(db_path)
    try:
        loaders.load_manifest(conn)
        report = {
            "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "db_path": str(db_path),
            "loaders": {},
            "missing_default_sources": missing_sources(),
        }
        steps = [
            ("hpo_genes_to_disease", loaders.load_hpo_genes_to_disease),
            ("monarch_causal_gene_disease", loaders.load_monarch_causal_gene_disease),
            ("monarch_gene_pathway", loaders.load_monarch_gene_pathway),
            ("hpo_disease_phenotype", loaders.load_hpo_disease_phenotype),
            ("orphadata_diseases", loaders.load_orphadata_diseases),
            ("orphadata_genes", loaders.load_orphadata_genes),
            ("orphadata_phenotypes", loaders.load_orphadata_phenotypes),
        ]
        for name, fn in steps:
            report["loaders"][name] = fn(conn)
        loaders.seed_gene_aliases(conn)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('built_at', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (report["built_at"],),
        )
        conn.commit()
        report["counts"] = _counts(conn)
        return report
    finally:
        conn.close()


def _counts(conn: sqlite3.Connection) -> dict:
    tables = [
        "disease",
        "gene",
        "pathway",
        "phenotype",
        "edge_gene_disease",
        "edge_disease_phenotype",
        "edge_gene_pathway",
    ]
    out = {}
    for table in tables:
        out[table] = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
    return out


def main() -> int:
    report = build_atlas()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
