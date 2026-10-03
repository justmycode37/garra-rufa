"""Offline tests for anchor packet assembly (mocked HTTP)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from garra.connections import build_connections
from garra.packet import cache as disk
from garra.packet.build import build_connections_packet, run_pipeline
from garra.packet.fetch import orpha_from_mondo_entity, orphadata_phenotypes
from garra.packet.prefetch import load_jobs_from_raresource, load_queries_from_file


class PrefetchLoadTests(unittest.TestCase):
    def test_load_anchors_file_skips_comments(self):
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write("# comment\nAlpha\n\nBeta\n")
            path = Path(fh.name)
        try:
            self.assertEqual(
                [j.query for j in load_queries_from_file(path)], ["Alpha", "Beta"]
            )
        finally:
            path.unlink()

    def test_raresource_requires_limit_bounds(self):
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
            fh.write(json.dumps({"disease_name": "Example disease"}) + "\n")
            path = Path(fh.name)
        try:
            with self.assertRaises(ValueError):
                load_jobs_from_raresource(path, limit=0)
            self.assertEqual(
                [j.query for j in load_jobs_from_raresource(path, limit=1)],
                ["Example disease"],
            )
        finally:
            path.unlink()


class CacheRegistryTests(unittest.TestCase):
    def test_packet_with_legacy_evidence_labels_is_not_reused(self):
        packet = {
            "schema_version": 1,
            "anchor": {"id": "MONDO:1", "name": "Test"},
            "candidates": [{}],
            "evidence": [],
            "fetch_meta": {"query": "Test"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "MONDO_1"
            base.mkdir()
            (base / "packet.json").write_text(json.dumps(packet), encoding="utf-8")
            disk.write_json(base / "manifest.json", {"anchor_id": "MONDO:1"}, retrieved_at="t")
            with patch.object(disk, "CACHE_ROOT", Path(tmp)):
                with patch.object(disk, "anchor_dir", lambda aid: Path(tmp) / "MONDO_1"):
                    self.assertFalse(
                        disk.packet_is_complete(packet, candidate_limit=5, semsim_limit=20)
                    )


class OrphadataParseTests(unittest.TestCase):
    def test_orpha_from_orphanet_xref(self):
        entity = {"xref": ["MONDO:0009290", "Orphanet:365", "OMIM:232300"]}
        self.assertEqual(orpha_from_mondo_entity(entity), "ORPHA:365")

    @patch("garra.packet.fetch._get_json")
    def test_orphadata_phenotype_shape(self, mock_get):
        mock_get.return_value = {
            "data": {
                "results": {
                    "Disorder": {
                        "HPODisorderAssociation": [
                            {
                                "HPOFrequency": "Very frequent (99-80%)",
                                "HPO": {"HPOId": "HP:0001252", "HPOTerm": "Hypotonia"},
                            },
                            {"HPOFrequency": "Excluded (0%)", "HPO": {"HPOId": "HP:0000001"}},
                        ]
                    }
                }
            }
        }
        out = orphadata_phenotypes("ORPHA:365")
        self.assertEqual(out["hpo_ids"], ["HP:0001252"])


class PacketBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_root = Path(self.tmp.name) / "cache"
        registry_patcher = patch.object(disk, "REGISTRY_PATH", self.cache_root / "query_registry.json")
        registry_patcher.start()
        self.addCleanup(registry_patcher.stop)
        patcher = patch.object(disk, "CACHE_ROOT", self.cache_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _search(self, q, *, use_cache=True):
        return (
            {
                "retrieved_at": "2026-10-04T00:00:00Z",
                "query": q,
                "item": {"id": "MONDO:0009290", "name": "glycogen storage disease II"},
            },
            True,
        )

    def _fake_fetch(self):
        return patch.multiple(
            "garra.packet.build",
            resolve_anchor_query=self._search,
            monarch_entity=lambda _id: {
                "retrieved_at": "2026-10-04T00:00:00Z",
                "entity": {"id": "MONDO:0009290", "xref": ["Orphanet:365"]},
            },
            monarch_disease_phenotypes=lambda _id: {
                "retrieved_at": "2026-10-04T00:00:00Z",
                "hpo_ids": ["HP:0001252", "HP:0001638"],
            },
            monarch_disease_genes=lambda _id: {
                "retrieved_at": "2026-10-04T00:00:00Z",
                "gene_symbols": ["GAA"],
            },
            orphadata_phenotypes=lambda _o: {
                "retrieved_at": "2026-10-04T00:00:00Z",
                "hpo_ids": ["HP:0001252"],
            },
            orphadata_genes=lambda _o: {
                "retrieved_at": "2026-10-04T00:00:00Z",
                "gene_symbols": ["GAA"],
            },
            monarch_semsim=lambda hps, limit=20: {
                "retrieved_at": "2026-10-04T00:00:01Z",
                "metric": "jaccard_similarity",
                "release": "unknown",
                "results": [
                    {
                        "id": "MONDO:0017694",
                        "name": "infantile Pompe",
                        "value": 0.75,
                        "matched_phenotypes": [],
                    }
                ],
            },
            opentargets_pathways_for_symbol=lambda sym: {
                "retrieved_at": "2026-10-04T00:00:02Z",
                "symbol": sym,
                "pathway_ids": ["R-HSA-70263"],
                "pathways": [{"pathwayId": "R-HSA-70263", "pathway": "Glycogen breakdown"}],
            },
        )

    def test_packet_validates_in_build_connections(self):
        with self._fake_fetch():
            packet = build_connections_packet("Pompe disease", use_cache=False, candidate_limit=1)
        self.assertEqual(packet["schema_version"], 1)
        self.assertEqual(packet["anchor"]["id"], "MONDO:0009290")
        self.assertTrue(packet["candidates"])
        result = build_connections(packet, min_score=50, limit=5)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["cards"])
        self.assertEqual(result["cards"][0]["biological_status"], "hypothesis_only")
        self.assertTrue(all(not c["reviewed"] for c in packet["candidates"][0]["claims"]))

    def test_current_packet_is_reused_without_network(self):
        with self._fake_fetch():
            packet = build_connections_packet("Pompe disease", use_cache=False, candidate_limit=1)
        with patch("garra.packet.build.resolve_anchor_query", side_effect=AssertionError("network")):
            cached = build_connections_packet("Pompe disease", candidate_limit=1)
        self.assertEqual(cached, packet)

    def test_writes_cache_manifest(self):
        with self._fake_fetch():
            build_connections_packet("Pompe disease", use_cache=False, candidate_limit=1)
        manifest_path = self.cache_root / "MONDO_0009290" / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text())
        self.assertIn("retrieved_at", manifest)
        self.assertIsNotNone(manifest["body"]["steps"]["orphadata_phenotypes"])


class PipelineFailureTests(unittest.TestCase):
    def test_failed_connections_and_explanation_do_not_report_success(self):
        with (
            patch("garra.packet.build.build_connections_packet", return_value={}),
            patch("garra.packet.build.build_connections", side_effect=ValueError("invalid packet")),
            patch("garra.packet.build.build_journey", return_value={"status": "error", "message": "failed"}),
        ):
            result = run_pipeline("test", live=False)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["connections"]["status"], "error")

    def test_successful_connections_and_explanation_report_success(self):
        with (
            patch("garra.packet.build.build_connections_packet", return_value={}),
            patch("garra.packet.build.build_connections", return_value={"status": "ok"}),
            patch("garra.packet.build.build_journey", return_value={"status": "ok"}),
            patch("garra.packet.build.explain_journey", return_value={"status": "ok"}),
        ):
            self.assertEqual(run_pipeline("test", live=False)["status"], "ok")

    def test_explanation_failure_does_not_report_success(self):
        with (
            patch("garra.packet.build.build_connections_packet", return_value={}),
            patch("garra.packet.build.build_connections", return_value={"status": "ok"}),
            patch("garra.packet.build.build_journey", return_value={"status": "ok"}),
            patch("garra.packet.build.explain_journey", return_value={"status": "error"}),
        ):
            self.assertEqual(run_pipeline("test", live=False)["status"], "error")

    def test_registry_rebuild_on_fresh_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "missing" / "connections"
            with (
                patch.object(disk, "CACHE_ROOT", root),
                patch.object(disk, "REGISTRY_PATH", root / "query_registry.json"),
            ):
                result = disk.rebuild_registry_from_disk()
                self.assertEqual(result["queries"], {})
                self.assertEqual(json.loads(disk.REGISTRY_PATH.read_text())["queries"], {})
