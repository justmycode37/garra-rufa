"""Atlas ingest, resolve, and similarity (uses data/raw when present)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from garra.graph.resolve import resolve_query
from garra.ingest.build import build_atlas
from garra.similarity import similar_diseases


class AtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls._tmp.name) / "atlas.sqlite"
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

    def test_build_report_json_serializable(self):
        json.dumps(self.report)


if __name__ == "__main__":
    unittest.main()
