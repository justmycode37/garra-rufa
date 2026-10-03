"""Read-only access to the local atlas SQLite database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from garra.paths import ATLAS_DB


class AtlasStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or ATLAS_DB
        if not self.db_path.is_file():
            raise FileNotFoundError(
                f"Atlas database not found at {self.db_path}. Run: PYTHONPATH=src python -m garra.atlas build"
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def genes_for_disease(self, disease_key: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT g.gene_key, g.symbol, g.ncbi_id, g.hgnc_id
                FROM edge_gene_disease e
                JOIN gene g ON g.gene_key = e.gene_key
                WHERE e.disease_key = ?
                ORDER BY g.symbol
                """,
                (disease_key,),
            ).fetchall()

    def diseases_for_gene(self, gene_key: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT d.disease_key, d.primary_name, d.namespace
                FROM edge_gene_disease e
                JOIN disease d ON d.disease_key = e.disease_key
                WHERE e.gene_key = ?
                ORDER BY d.primary_name
                """,
                (gene_key,),
            ).fetchall()

    def phenotypes_for_disease(self, disease_key: str) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT hpo_id FROM edge_disease_phenotype WHERE disease_key = ?",
                (disease_key,),
            ).fetchall()
        return {row["hpo_id"] for row in rows}

    def pathways_for_gene(self, gene_key: str) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT pathway_key FROM edge_gene_pathway WHERE gene_key = ?",
                (gene_key,),
            ).fetchall()
        return {row["pathway_key"] for row in rows}

    def pathways_for_disease(self, disease_key: str) -> set[str]:
        pathways: set[str] = set()
        for gene in self.genes_for_disease(disease_key):
            pathways |= self.pathways_for_gene(gene["gene_key"])
        return pathways
