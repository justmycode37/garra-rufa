"""Atlas ingest, resolve, and similarity using small offline source fixtures."""

from __future__ import annotations

import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from garra.graph.resolve import resolve_query
from garra.graph.store import AtlasStore
from garra.ingest.build import build_atlas
from garra.similarity import similar_diseases
from garra.sources.catalog import BULK_SOURCES


class AtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls._tmp.name) / "atlas.sqlite"
        cls.raw = Path(cls._tmp.name) / "raw"

        def source_path(source_id, filename):
            return cls.raw / source_id / filename

        def write_source(source_id, text):
            source = BULK_SOURCES[source_id]
            path = source_path(source_id, source.filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".gz":
                with gzip.open(path, "wt") as handle:
                    handle.write(text)
            else:
                path.write_text(text)

        write_source(
            "hpo_genes_to_disease",
            "ncbi_gene_id\tgene_symbol\tdisease_id\tassociation_type\n"
            "2548\tGAA\tMONDO:0009290\tcausal\n",
        )
        write_source(
            "monarch_causal_gene_to_disease",
            "subject\tsubject_label\tobject\tobject_label\n"
            "HGNC:4065\tGAA\tMONDO:0009290\tPompe disease\n"
            "HGNC:9999\tTEST\tMONDO:9999999\tSynthetic neighbor\n",
        )
        write_source(
            "monarch_gene_to_pathway",
            "subject_label\tobject\tobject_label\n"
            "GAA\tTEST:pathway\tSynthetic pathway\n"
            "TEST\tTEST:pathway\tSynthetic pathway\n",
        )
        with (
            patch("garra.ingest.loaders.raw_file", source_path),
            patch("garra.ingest.loaders.RAW_DIR", cls.raw),
            patch("garra.ingest.build.RAW_DIR", cls.raw),
        ):
            cls.report = build_atlas(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_build_produces_counts(self):
        self.assertGreater(self.report["counts"]["gene"], 0)
        self.assertGreater(self.report["counts"]["edge_gene_disease"], 0)

    def test_resolve_pompe(self):
        out = resolve_query("Pompe disease", db_path=self.db)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["matches"])

    def test_similar_by_gene_symbol(self):
        out = similar_diseases(gene_key="SYMBOL:GAA", limit=5, db_path=self.db)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["neighbors"] or out["anchor"]["genes"] == ["GAA"])

    def test_store_is_read_only_and_closes_connection(self):
        store = AtlasStore(self.db)
        with store.connect() as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM disease")
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_build_report_json_serializable(self):
        json.dumps(self.report)


if __name__ == "__main__":
    unittest.main()
